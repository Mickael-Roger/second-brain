"""FreshRSS Google-Reader-compatible API client.

Replaces the old Fever client. The GReader API lives at
`<base>/api/greader.php/...` and uses a two-token auth model:

  - ``Auth`` token from ``/accounts/ClientLogin`` (POST Email/Passwd).
    Valid ~7 days. Sent as ``Authorization: GoogleLogin auth=<token>``.
  - ``T`` (POST CSRF) token from ``GET /reader/api/0/token``. Valid
    ~30 min. Sent as the form parameter ``T=<token>`` on every write.

Both refresh lazily on ``HTTP 401``: ClientLogin retries once, the
``T`` token refreshes when FreshRSS returns
``X-Reader-Google-Bad-Token: true``.

Stream id vocabulary
--------------------
``s=<stream>`` accepts:

  - ``feed/<id>`` — a single feed
  - ``user/-/state/com.google/reading-list`` — all subscribed items
  - ``user/-/state/com.google/read`` — items flagged read
  - ``user/-/state/com.google/starred`` — favourites
  - ``user/-/label/<name>`` — a folder OR a label (FreshRSS overloads
    the namespace; discriminate by intersecting with
    ``subscription/list`` membership when it matters)

Item ids
--------
Two interchangeable forms:

  - long  ``tag:google.com,2005:reader/item/<16-hex>`` (returned in JSON)
  - short ``<decimal>`` or ``<hex>`` (accepted by ``edit-tag``)

We canonicalise to the **lowercased 16-char zero-padded hex string**
on ingest so the existing ``news_articles.external_id`` UNIQUE
constraint keeps working across re-fetches.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

STATE_READ = "user/-/state/com.google/read"
STATE_STARRED = "user/-/state/com.google/starred"
STATE_READING_LIST = "user/-/state/com.google/reading-list"
LABEL_PREFIX = "user/-/label/"

# Long-form id prefix returned in stream items.
_ITEM_TAG_PREFIX = "tag:google.com,2005:reader/item/"


# ── DTOs ────────────────────────────────────────────────────────────


@dataclass(slots=True)
class GReaderItem:
    id: str                       # canonical 16-char lowercase hex
    feed_id: str                  # numeric, e.g. "42" (we strip "feed/")
    title: str
    author: str | None
    html: str
    url: str | None               # alternate[0].href
    is_read: bool
    is_starred: bool
    labels: list[str]             # user/-/label/<name> with <name> not a folder
    created_on_time: int          # unix seconds (from `published`)


@dataclass(slots=True)
class GReaderFeed:
    id: str                       # numeric, "feed/" stripped
    title: str
    site_url: str | None
    group_id: str | None          # we use the category name as the id (GReader has no numeric folder id)
    group_name: str | None
    icon_url: str | None          # URL we fetch + cache server-side (or None)


@dataclass(slots=True)
class GReaderTag:
    """A `tag/list` entry. FreshRSS reports both folders and labels
    here; both come back as ``user/-/label/<name>`` ids. ``type`` is
    ``"folder"`` when the entry also appears as a category in
    ``subscription/list``, ``"label"`` otherwise. We compute it
    ourselves in :meth:`GReaderClient.tags` because raw FreshRSS output
    is unreliable about a typed field."""

    id: str                       # "user/-/label/<name>"
    name: str                     # the bare <name>
    type: str = "label"           # "folder" | "label"
    sortid: str | None = None


# ── Errors ──────────────────────────────────────────────────────────


class GReaderAuthError(RuntimeError):
    pass


class GReaderError(RuntimeError):
    pass


# ── Client ──────────────────────────────────────────────────────────


class GReaderClient:
    """Async wrapper over FreshRSS's GReader API.

    Use as an async context manager. Each entry does ClientLogin
    eagerly so the first read finds a valid Auth token; the CSRF (T)
    token is fetched lazily on the first write."""

    def __init__(self, *, base_url: str, username: str, password: str) -> None:
        # Accept either a bare FreshRSS root (preferred) or a legacy
        # ``…/api/fever.php`` URL — strip the Fever bits so existing
        # configs still resolve while users migrate.
        root = base_url.rstrip("/?&")
        for suffix in ("/api/fever.php", "/api/greader.php"):
            if root.endswith(suffix):
                root = root[: -len(suffix)]
                break
        self.base_url = root
        self.username = username
        self.password = password
        self._client: httpx.AsyncClient | None = None
        self._auth_token: str | None = None
        self._csrf_token: str | None = None
        # Folder names seen in subscription/list, used to classify
        # ``user/-/label/<x>`` as folder vs label on item ingest.
        self._folder_names: set[str] = set()

    # -- lifecycle --------------------------------------------------

    async def __aenter__(self) -> "GReaderClient":
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        await self._client_login()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- auth -------------------------------------------------------

    async def _client_login(self) -> None:
        assert self._client is not None
        url = f"{self.base_url}/api/greader.php/accounts/ClientLogin"
        resp = await self._client.post(
            url, data={"Email": self.username, "Passwd": self.password}
        )
        if resp.status_code != 200:
            raise GReaderAuthError(
                f"ClientLogin returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        for line in resp.text.splitlines():
            if line.startswith("Auth="):
                self._auth_token = line[len("Auth="):].strip()
                break
        if not self._auth_token:
            raise GReaderAuthError("ClientLogin response missing Auth=")

    async def _fetch_csrf(self) -> None:
        assert self._client is not None and self._auth_token is not None
        url = f"{self.base_url}/api/greader.php/reader/api/0/token"
        resp = await self._client.get(url, headers=self._auth_headers())
        if resp.status_code != 200:
            raise GReaderAuthError(
                f"token endpoint returned HTTP {resp.status_code}"
            )
        self._csrf_token = resp.text.strip()
        if not self._csrf_token:
            raise GReaderAuthError("token endpoint returned empty body")

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"GoogleLogin auth={self._auth_token}"}

    # -- low-level transport ----------------------------------------

    async def _get(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> httpx.Response:
        """GET with one auto-retry on 401 (re-login). Returns the response;
        caller decides how to parse it (JSON vs plain text)."""
        assert self._client is not None
        url = f"{self.base_url}/api/greader.php{path}"
        for attempt in (1, 2):
            resp = await self._client.get(
                url, params=params, headers=self._auth_headers()
            )
            if resp.status_code != 401:
                resp.raise_for_status()
                return resp
            if attempt == 2:
                raise GReaderAuthError(
                    f"GET {path} still 401 after re-login"
                )
            await self._client_login()
        raise AssertionError("unreachable")

    async def _post_write(
        self, path: str, *, params: dict[str, str | list[str]]
    ) -> httpx.Response:
        """POST a write action with the ``T=<csrf>`` form param injected.
        Refreshes the CSRF token on ``X-Reader-Google-Bad-Token`` and
        the Auth token on plain 401, each with a single retry."""
        assert self._client is not None
        if self._csrf_token is None:
            await self._fetch_csrf()
        url = f"{self.base_url}/api/greader.php{path}"
        for attempt in (1, 2, 3):
            # httpx 0.28 requires `data=` to be a Mapping; a list of
            # tuples gets misrouted into encode_content() and produces a
            # sync IteratorByteStream that AsyncClient refuses to send.
            # Dict values that are lists are expanded into repeated form
            # keys by httpx itself, so passing the dict directly works.
            form: dict[str, str | list[str]] = dict(params)
            form["T"] = self._csrf_token or ""
            resp = await self._client.post(
                url, data=form, headers=self._auth_headers()
            )
            if resp.status_code == 200:
                return resp
            if resp.status_code == 401:
                bad_token = (
                    resp.headers.get("X-Reader-Google-Bad-Token", "").lower()
                    == "true"
                )
                if bad_token and attempt < 3:
                    await self._fetch_csrf()
                    continue
                if attempt < 3:
                    await self._client_login()
                    await self._fetch_csrf()
                    continue
            raise GReaderError(
                f"POST {path} failed: HTTP {resp.status_code} {resp.text[:200]}"
            )
        raise AssertionError("unreachable")

    async def _get_json(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        params = {**(params or {}), "output": "json"}
        resp = await self._get(path, params=params)
        body = resp.json()
        if not isinstance(body, dict):
            raise GReaderError(
                f"GET {path} returned non-object body: {type(body).__name__}"
            )
        return body

    # -- reads ------------------------------------------------------

    async def subscriptions(self) -> dict[str, GReaderFeed]:
        """Return feed_id → GReaderFeed. Side-effect: refreshes the
        internal folder-name set used by item ingest to discriminate
        folder vs label categories."""
        body = await self._get_json("/reader/api/0/subscription/list")
        out: dict[str, GReaderFeed] = {}
        folders: set[str] = set()
        for raw in body.get("subscriptions") or []:
            sid = str(raw.get("id") or "")          # "feed/<id>"
            fid = sid[len("feed/"):] if sid.startswith("feed/") else sid
            cats = raw.get("categories") or []
            # A feed has 0..1 folder in FreshRSS; pick the first.
            group_name: str | None = None
            for c in cats:
                label = c.get("label") or ""
                if label:
                    group_name = str(label)
                    folders.add(group_name)
                    break
            out[fid] = GReaderFeed(
                id=fid,
                title=str(raw.get("title") or fid),
                site_url=raw.get("htmlUrl") or None,
                group_id=group_name,
                group_name=group_name,
                icon_url=raw.get("iconUrl") or None,
            )
        self._folder_names = folders
        return out

    async def tags(self) -> list[GReaderTag]:
        """Return the user's tags (folders + labels). Folder/label
        classification uses the cached folder set from
        :meth:`subscriptions` — call it first when accuracy matters."""
        body = await self._get_json("/reader/api/0/tag/list")
        out: list[GReaderTag] = []
        for raw in body.get("tags") or []:
            tid = str(raw.get("id") or "")
            if not tid.startswith(LABEL_PREFIX):
                continue
            name = tid[len(LABEL_PREFIX):]
            ttype = "folder" if name in self._folder_names else "label"
            out.append(GReaderTag(
                id=tid, name=name, type=ttype,
                sortid=str(raw["sortid"]) if "sortid" in raw else None,
            ))
        return out

    async def unread_counts(self) -> dict[str, int]:
        """Stream id → unread count (e.g. ``feed/42`` → 7)."""
        body = await self._get_json("/reader/api/0/unread-count")
        out: dict[str, int] = {}
        for r in body.get("unreadcounts") or []:
            sid = str(r.get("id") or "")
            try:
                out[sid] = int(r.get("count") or 0)
            except (TypeError, ValueError):
                continue
        return out

    async def items_since(
        self, *, since_ts: int, max_items: int = 500, page_size: int = 100,
    ) -> list[GReaderItem]:
        """Return at most ``max_items`` items strictly newer than
        ``since_ts`` (unix seconds), oldest-first. Used for incremental
        fetches.

        Implementation: ``stream/contents/reading-list`` with
        ``ot=since_ts`` (items with timestamp > since), ``r=o``
        (oldest first), and continuation pagination."""
        out: list[GReaderItem] = []
        continuation: str | None = None
        # +1 because GReader's ``ot=`` is inclusive on some forks; we
        # de-dup on ``external_id`` upstream so an overlap is fine.
        params_base = {
            "s": STATE_READING_LIST,
            "n": str(page_size),
            "r": "o",
            "ot": str(max(0, since_ts)),
        }
        for _ in range(max(1, (max_items + page_size - 1) // page_size + 2)):
            params = dict(params_base)
            if continuation:
                params["c"] = continuation
            body = await self._get_json(
                "/reader/api/0/stream/contents/user/-/state/com.google/reading-list",
                params=params,
            )
            for raw in body.get("items") or []:
                out.append(self._parse_item(raw))
                if len(out) >= max_items:
                    return out
            continuation = (body.get("continuation") or "").strip() or None
            if continuation is None:
                break
        return out

    async def items_in_range(
        self,
        *,
        from_ts: int,
        to_ts: int | None = None,
        max_items: int = 100_000,
        max_pages: int = 1000,
        page_size: int = 100,
    ) -> list[GReaderItem]:
        """Walk items newest-first via continuation. Returns items
        whose ``published`` falls in ``[from_ts, to_ts]``. ``to_ts=None``
        means "no upper bound" (up to now). Stops when a whole page
        falls below ``from_ts`` (same heuristic as the old Fever
        client). Returned items are newest-first."""
        out: list[GReaderItem] = []
        continuation: str | None = None
        params_base = {
            "s": STATE_READING_LIST,
            "n": str(page_size),
            "ot": str(max(0, from_ts)),
        }
        if to_ts is not None:
            params_base["nt"] = str(to_ts)
        pages = 0
        for _ in range(max_pages):
            params = dict(params_base)
            if continuation:
                params["c"] = continuation
            body = await self._get_json(
                "/reader/api/0/stream/contents/user/-/state/com.google/reading-list",
                params=params,
            )
            items = body.get("items") or []
            if not items:
                break
            pages += 1
            page_all_below_floor = True
            for raw in items:
                item = self._parse_item(raw)
                ts = item.created_on_time
                if ts and ts < from_ts:
                    continue
                page_all_below_floor = False
                if to_ts is not None and ts and ts > to_ts:
                    continue
                out.append(item)
                if len(out) >= max_items:
                    log.info(
                        "greader items_in_range: hit max_items=%d (pages=%d)",
                        max_items, pages,
                    )
                    return out
            if page_all_below_floor:
                break
            continuation = (body.get("continuation") or "").strip() or None
            if continuation is None:
                break
        log.info(
            "greader items_in_range: walked %d pages, %d items collected",
            pages, len(out),
        )
        return out

    async def unread_item_ids(self, *, max_ids: int = 50_000) -> list[str]:
        """Every unread item id on the server. Used by the completeness
        + reconciliation pass. Returned ids are canonical hex."""
        out: list[str] = []
        continuation: str | None = None
        page_size = min(10_000, max_ids)
        for _ in range(20):  # 200k cap is plenty for FreshRSS scales
            params: dict[str, str] = {
                "s": STATE_READING_LIST,
                "xt": STATE_READ,
                "n": str(page_size),
            }
            if continuation:
                params["c"] = continuation
            body = await self._get_json(
                "/reader/api/0/stream/items/ids", params=params
            )
            for r in body.get("itemRefs") or []:
                rid = r.get("id")
                if rid is None:
                    continue
                out.append(_short_id_to_hex(str(rid)))
                if len(out) >= max_ids:
                    return out
            continuation = (body.get("continuation") or "").strip() or None
            if continuation is None:
                break
        return out

    async def starred_item_ids(self, *, max_ids: int = 50_000) -> list[str]:
        """Every starred item id on the server. Used to reconcile
        is_starred across machines / web UI changes."""
        out: list[str] = []
        continuation: str | None = None
        page_size = min(10_000, max_ids)
        for _ in range(20):
            params: dict[str, str] = {
                "s": STATE_STARRED,
                "n": str(page_size),
            }
            if continuation:
                params["c"] = continuation
            body = await self._get_json(
                "/reader/api/0/stream/items/ids", params=params
            )
            for r in body.get("itemRefs") or []:
                rid = r.get("id")
                if rid is None:
                    continue
                out.append(_short_id_to_hex(str(rid)))
                if len(out) >= max_ids:
                    return out
            continuation = (body.get("continuation") or "").strip() or None
            if continuation is None:
                break
        return out

    async def items_by_ids(
        self, ids: list[str], *, batch_size: int = 250,
    ) -> list[GReaderItem]:
        """Fetch full items by id. Accepts either hex or decimal ids;
        the server understands both. Returned items use canonical hex."""
        out: list[GReaderItem] = []
        for i in range(0, len(ids), batch_size):
            batch = ids[i : i + batch_size]
            if not batch:
                continue
            # ``stream/items/contents`` accepts a POST body with
            # repeated ``i=`` params — way more URL-budget friendly
            # than a GET querystring for big batches.
            params = {"i": batch, "output": "json"}
            resp = await self._post_write(
                "/reader/api/0/stream/items/contents", params=params
            )
            body = resp.json()
            for raw in (body.get("items") or []):
                out.append(self._parse_item(raw))
        return out

    # -- writes: item state -----------------------------------------

    async def edit_tag(
        self,
        *,
        item_ids: Iterable[str],
        add: Iterable[str] = (),
        remove: Iterable[str] = (),
    ) -> None:
        ids = list(item_ids)
        if not ids:
            return
        add_list = list(add)
        remove_list = list(remove)
        if not add_list and not remove_list:
            return
        params: dict[str, str | list[str]] = {"i": ids}
        if add_list:
            params["a"] = add_list
        if remove_list:
            params["r"] = remove_list
        await self._post_write("/reader/api/0/edit-tag", params=params)

    async def mark_read(self, item_id: str) -> None:
        await self.edit_tag(item_ids=[item_id], add=[STATE_READ])

    async def mark_unread(self, item_id: str) -> None:
        # FreshRSS has no positive "kept-unread" — unread is "not read".
        await self.edit_tag(item_ids=[item_id], remove=[STATE_READ])

    async def set_starred(self, item_id: str, *, starred: bool) -> None:
        if starred:
            await self.edit_tag(item_ids=[item_id], add=[STATE_STARRED])
        else:
            await self.edit_tag(item_ids=[item_id], remove=[STATE_STARRED])

    async def add_label(self, item_id: str, label: str) -> None:
        await self.edit_tag(item_ids=[item_id], add=[LABEL_PREFIX + label])

    async def remove_label(self, item_id: str, label: str) -> None:
        await self.edit_tag(item_ids=[item_id], remove=[LABEL_PREFIX + label])

    async def mark_stream_all_read(
        self, stream_id: str, *, older_than_us: int | None = None,
    ) -> None:
        params: dict[str, str | list[str]] = {"s": stream_id}
        if older_than_us is not None:
            params["ts"] = str(older_than_us)
        await self._post_write(
            "/reader/api/0/mark-all-as-read", params=params
        )

    # -- writes: subscriptions --------------------------------------

    async def subscribe(
        self,
        url: str,
        *,
        title: str | None = None,
        category: str | None = None,
    ) -> str:
        """Subscribe to ``url`` via FreshRSS's autodiscovery
        (``subscription/quickadd``). If ``category`` is given, file the
        new feed into ``user/-/label/<category>`` afterwards. Returns
        the new feed's stream id ``feed/<id>``."""
        resp = await self._post_write(
            "/reader/api/0/subscription/quickadd",
            params={"quickadd": url},
        )
        body = resp.json() if resp.headers.get("content-type", "").startswith(
            "application/json"
        ) else {}
        stream_id = str(body.get("streamId") or "")
        if not stream_id.startswith("feed/"):
            raise GReaderError(
                f"quickadd did not return a streamId: {body!r}"
            )
        # Optional title + folder fix-up.
        edit_params: dict[str, str | list[str]] = {
            "ac": "edit",
            "s": stream_id,
        }
        changed = False
        if title:
            edit_params["t"] = title
            changed = True
        if category:
            edit_params["a"] = LABEL_PREFIX + category
            changed = True
        if changed:
            await self._post_write(
                "/reader/api/0/subscription/edit", params=edit_params
            )
        return stream_id

    async def unsubscribe(self, feed_id: str) -> None:
        stream_id = feed_id if feed_id.startswith("feed/") else f"feed/{feed_id}"
        await self._post_write(
            "/reader/api/0/subscription/edit",
            params={"ac": "unsubscribe", "s": stream_id},
        )

    async def edit_subscription(
        self,
        feed_id: str,
        *,
        title: str | None = None,
        add_category: str | None = None,
        remove_category: str | None = None,
    ) -> None:
        """Rename and/or move a feed. ``add_category=None`` skips the
        move; passing an empty string is rejected by FreshRSS so the
        caller should use :meth:`remove_category_from_feed` explicitly
        when the goal is to detach without re-attaching."""
        stream_id = feed_id if feed_id.startswith("feed/") else f"feed/{feed_id}"
        params: dict[str, str | list[str]] = {
            "ac": "edit",
            "s": stream_id,
        }
        if title is not None:
            params["t"] = title
        if add_category is not None:
            params["a"] = LABEL_PREFIX + add_category
        if remove_category is not None:
            params["r"] = LABEL_PREFIX + remove_category
        if len(params) == 2:
            return  # nothing to change
        await self._post_write(
            "/reader/api/0/subscription/edit", params=params
        )

    # -- writes: categories -----------------------------------------

    async def rename_category(self, old: str, new: str) -> None:
        await self._post_write(
            "/reader/api/0/rename-tag",
            params={
                "s": LABEL_PREFIX + old,
                "dest": LABEL_PREFIX + new,
            },
        )

    async def delete_category(self, name: str) -> bool:
        """Try ``disable-tag``. If FreshRSS responds with 404 the
        endpoint isn't wired up on this instance — return False so the
        caller can fall back to removing the label from every member
        feed. Any other failure raises."""
        assert self._client is not None
        url = f"{self.base_url}/api/greader.php/reader/api/0/disable-tag"
        if self._csrf_token is None:
            await self._fetch_csrf()
        resp = await self._client.post(
            url,
            data={"s": LABEL_PREFIX + name, "T": self._csrf_token or ""},
            headers=self._auth_headers(),
        )
        if resp.status_code == 404:
            return False
        if resp.status_code == 200:
            return True
        if resp.status_code == 401:
            await self._client_login()
            await self._fetch_csrf()
            resp = await self._client.post(
                url,
                data={"s": LABEL_PREFIX + name, "T": self._csrf_token or ""},
                headers=self._auth_headers(),
            )
            if resp.status_code == 200:
                return True
            if resp.status_code == 404:
                return False
        raise GReaderError(
            f"disable-tag failed: HTTP {resp.status_code} {resp.text[:200]}"
        )

    # -- parsing ----------------------------------------------------

    def _parse_item(self, raw: dict[str, Any]) -> GReaderItem:
        rid = str(raw.get("id") or "")
        item_hex = _long_id_to_hex(rid)

        origin = raw.get("origin") or {}
        origin_stream = str(origin.get("streamId") or "")
        feed_id = (
            origin_stream[len("feed/"):]
            if origin_stream.startswith("feed/")
            else origin_stream
        )

        # Prefer `content`, fall back to `summary`. Both are
        # ``{direction, content}`` — only `.content` is interesting.
        body_html = ""
        for key in ("content", "summary"):
            blob = raw.get(key)
            if isinstance(blob, dict):
                inner = blob.get("content")
                if isinstance(inner, str) and inner:
                    body_html = inner
                    break

        url = None
        for alt in raw.get("alternate") or []:
            href = (alt or {}).get("href")
            if href:
                url = str(href)
                break

        published = int(raw.get("published") or 0)
        if published <= 0:
            ts_us = str(raw.get("timestampUsec") or "")
            if ts_us.isdigit():
                published = int(ts_us) // 1_000_000

        categories = [str(c) for c in (raw.get("categories") or [])]
        is_read = any(_category_is_state(c, "read") for c in categories)
        is_starred = any(_category_is_state(c, "starred") for c in categories)
        labels = _extract_labels(categories, folders=self._folder_names)

        title = str(raw.get("title") or "").strip() or "(untitled)"
        author = raw.get("author")
        author = str(author).strip() if author else None
        if author == "":
            author = None

        return GReaderItem(
            id=item_hex,
            feed_id=feed_id,
            title=title,
            author=author,
            html=body_html,
            url=url,
            is_read=is_read,
            is_starred=is_starred,
            labels=labels,
            created_on_time=published,
        )


# ── Module-level helpers ────────────────────────────────────────────


def _long_id_to_hex(raw: str) -> str:
    """Normalise any item-id form to canonical 16-char lowercase hex.

    Accepts:
      - ``tag:google.com,2005:reader/item/<16-hex>`` → strip prefix
      - bare hex (any length) → zero-pad to 16
      - decimal int → format as 16-hex
    """
    s = raw.strip()
    if s.startswith(_ITEM_TAG_PREFIX):
        s = s[len(_ITEM_TAG_PREFIX):]
    s = s.lower()
    # Try decimal first — common short form. Only treat as decimal if
    # the string is pure digits AND doesn't fit a 16-hex slot already
    # (so a 16-char hex like "00000000abcdef01" doesn't get coerced).
    if s.isdigit() and len(s) != 16:
        try:
            return f"{int(s):016x}"
        except ValueError:
            pass
    # Hex (possibly short) — pad.
    if all(c in "0123456789abcdef" for c in s):
        return s.rjust(16, "0")
    # Last-resort fallback: leave as-is. Caller treats it as opaque.
    return s


def _short_id_to_hex(raw: str) -> str:
    """Same as :func:`_long_id_to_hex` but tuned for the short ids
    returned by ``stream/items/ids`` (typically decimal)."""
    return _long_id_to_hex(raw)


def hex_to_decimal(item_hex: str) -> str:
    """Inverse for places where we want the shorter form on a URL."""
    s = item_hex.strip().lower()
    if not s:
        return s
    return str(int(s, 16))


def _category_is_state(category: str, state: str) -> bool:
    # FreshRSS emits ``user/<n>/state/com.google/<state>``; we accept
    # any ``<n>`` because clients often see ``user/-`` or the numeric
    # user id depending on the endpoint.
    parts = category.split("/")
    return (
        len(parts) >= 5
        and parts[0] == "user"
        and parts[2] == "state"
        and parts[3] == "com.google"
        and parts[4] == state
    )


def _extract_labels(categories: list[str], *, folders: set[str]) -> list[str]:
    """Pull user labels out of a categories array, filtering out the
    folder names (which share the ``user/-/label/`` namespace).

    Stable order, deduplicated."""
    seen: set[str] = set()
    out: list[str] = []
    for c in categories:
        parts = c.split("/")
        # user/<n>/label/<name>
        if len(parts) < 4 or parts[0] != "user" or parts[2] != "label":
            continue
        name = "/".join(parts[3:])
        if not name or name in folders or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def published_iso(item: GReaderItem) -> str:
    """Convert GReader's unix ``created_on_time`` to ISO-8601 UTC,
    falling back to ``now`` if the field is missing/zero."""
    ts = item.created_on_time
    if ts <= 0:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def html_to_plain_text(html: str, *, max_len: int | None = None) -> str:
    """Strip HTML tags from an item body. The feed description is what
    the user's RSS pipeline has already produced — we just want the
    readable text out of it. Optional cap for previews."""
    import re

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    if max_len is not None and len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def extract_first_image(html: str) -> str | None:
    """Pull the first ``<img src="...">`` URL from a body. Used as a
    thumbnail in the article detail pane. Returns None if nothing
    matches."""
    import re

    if not html:
        return None
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    return m.group(1) if m else None
