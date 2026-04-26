"""Fever-API client for FreshRSS.

The Fever API is HTTP POST-only with form-encoded body; the auth token
is `api_key=md5(username:password)`. We only call two endpoints:

  - `?api&feeds`     — lookup table id → title
  - `?api&items`     — paginated items (50 per page); we walk by
                       `since_id` to incrementally pull new articles

Reference: https://feedafever.com/api  (cached at FreshRSS docs)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass(slots=True)
class FeverItem:
    id: str
    feed_id: str
    title: str
    author: str | None
    html: str            # the feed's body / description (may already be LLM-synthesised)
    url: str | None
    is_saved: bool
    is_read: bool
    created_on_time: int  # unix seconds


@dataclass(slots=True)
class FeverFeed:
    id: str
    title: str
    site_url: str | None
    group_id: str | None     # FreshRSS folder/category id (for include/exclude config)
    group_name: str | None   # human-readable folder name (for the UI)


class FeverClient:
    """Thin async wrapper over Fever endpoints.

    One client per fetch run. Re-use across runs is fine but not required —
    each instance opens its own httpx.AsyncClient when used as an async
    context manager.
    """

    def __init__(self, *, base_url: str, api_key: str) -> None:
        # Fever expects the form param `api_key` literally — never put it
        # in the URL or a header. Some FreshRSS deployments are picky about
        # the `api` query string being empty (no value), hence the bare flag.
        self.base_url = base_url.rstrip("?&")
        self.api_key = api_key
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "FeverClient":
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, *, params: dict[str, str]) -> dict[str, Any]:
        assert self._client is not None, "use as async context manager"
        # Fever wants the `api` flag in the query string AND `api_key` in
        # the form body. The `?api` flag is the trigger that switches the
        # endpoint into Fever-compatibility mode.
        url = f"{self.base_url}?api"
        for k, v in params.items():
            url += f"&{k}={v}"
        resp = await self._client.post(url, data={"api_key": self.api_key})
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict):
            raise RuntimeError(f"Fever returned non-object body: {type(body).__name__}")
        if int(body.get("auth", 0)) != 1:
            raise RuntimeError("Fever auth failed (check api_key)")
        return body

    async def mark_item_read(self, item_id: str) -> None:
        """Push read-state back to FreshRSS via Fever's `mark` action.
        Fever signature: `?api&mark=item&as=read&id=<external_id>`.
        We don't read the response — it just echoes the unread count."""
        await self._post(params={"mark": "item", "as": "read", "id": item_id})

    async def feeds(self) -> dict[str, FeverFeed]:
        """Return feed_id → FeverFeed (with group/folder name resolved).

        Fever's `feeds` action returns the feed list AND a separate
        `feed_groups` mapping (feed_id → list of group_ids). To get
        human-readable folder names we additionally call `groups` and
        join: feed → group_id → group_title."""
        body = await self._post(params={"feeds": ""})
        # Fever can return groups in the same response via `?api&feeds&groups`,
        # but our URL builder doesn't support multi-flag actions cleanly;
        # FreshRSS handles a second small request quickly enough.
        groups_body = await self._post(params={"groups": ""})

        # group_id → title
        group_titles: dict[str, str] = {}
        for raw in groups_body.get("groups", []) or []:
            gid = str(raw.get("id"))
            group_titles[gid] = str(raw.get("title", "")).strip() or gid

        # feed_id → first group_id (a feed CAN belong to multiple groups in
        # FreshRSS; we keep the first as the canonical folder name. The
        # bubble UI just needs one label per article).
        feed_to_group: dict[str, str] = {}
        for raw in body.get("feeds_groups", []) or []:
            gid = str(raw.get("group_id"))
            for fid in str(raw.get("feed_ids", "")).split(","):
                fid = fid.strip()
                if fid and fid not in feed_to_group:
                    feed_to_group[fid] = gid

        out: dict[str, FeverFeed] = {}
        for raw in body.get("feeds", []) or []:
            fid = str(raw.get("id"))
            gid = feed_to_group.get(fid)
            out[fid] = FeverFeed(
                id=fid,
                title=str(raw.get("title", "")) or fid,
                site_url=raw.get("site_url") or None,
                group_id=gid,
                group_name=group_titles.get(gid) if gid else None,
            )
        return out

    async def items_since(
        self,
        *,
        since_id: int = 0,
        max_items: int = 500,
    ) -> list[FeverItem]:
        """Return at most `max_items` items strictly newer than `since_id`,
        oldest-first. Walks Fever's 50-item pages until either the budget
        is exhausted, the API stops returning new ids, or we've fetched
        all unread items."""
        out: list[FeverItem] = []
        cursor = since_id
        # Fever returns at most 50 per call. Cap pages so we can't spin
        # forever if the server keeps echoing back ids we've already seen.
        max_pages = max(1, (max_items + 49) // 50)
        for _ in range(max_pages):
            body = await self._post(
                params={"items": "", "since_id": str(cursor)}
            )
            items = body.get("items") or []
            if not items:
                break
            for raw in items:
                out.append(_parse_item(raw))
                cursor = max(cursor, int(raw.get("id", 0)))
                if len(out) >= max_items:
                    return out
            if len(items) < 50:
                # last page — Fever returns fewer than 50 when caught up.
                break
        return out

    async def items_in_range(
        self,
        *,
        from_ts: int,
        to_ts: int | None = None,
        max_items: int = 500,
        max_pages: int = 200,
    ) -> list[FeverItem]:
        """Walk items newest-first via Fever's `max_id` cursor and return
        those whose `created_on_time` falls in [from_ts, to_ts].

        IMPORTANT: FreshRSS's Fever item ids are based on FETCH time
        (microsecond timestamp at the moment FreshRSS imported the
        article), NOT publication time. That means id-DESC order does
        NOT correspond to publication-time order — a feed that
        re-publishes an old article today gets a fresh high id with an
        old `created_on_time`. So we cannot break early when we see
        an out-of-window item: there may still be in-window items
        below it in the id walk. We page all the way through.

        Two caps protect us from runaway walks on huge backlogs:
        - `max_items`: once we have this many in-window items, stop.
        - `max_pages`: hard cap on Fever calls (50 items each). Past
          this we give up walking and trust the cron's incremental
          fetches to fill in any gaps next time.

        `to_ts=None` means "no upper bound" (i.e. up to now). Returned
        items are newest-first."""
        out: list[FeverItem] = []
        # Seed with a value larger than any plausible Fever item id.
        # FreshRSS uses microsecond-timestamp ids (~1.78e15 for 2026),
        # so 2^63-1 is comfortably above for centuries.
        cursor: int = 2**63 - 1
        pages = 0
        items_seen = 0
        for _ in range(max_pages):
            body = await self._post(
                params={"items": "", "max_id": str(cursor)}
            )
            items = body.get("items") or []
            if not items:
                break
            pages += 1
            items_seen += len(items)
            for raw in items:
                item = _parse_item(raw)
                ts = item.created_on_time
                rid = int(raw.get("id", 0))
                if rid < cursor:
                    cursor = rid
                if ts and ts < from_ts:
                    # Out of window on the older side — skip but keep walking.
                    continue
                if to_ts is not None and ts and ts > to_ts:
                    # Future relative to the requested window — skip.
                    continue
                out.append(item)
                if len(out) >= max_items:
                    log.info(
                        "fever items_in_range: hit max_items=%d after %d pages "
                        "(items_seen=%d)",
                        max_items, pages, items_seen,
                    )
                    return out
            if len(items) < 50:
                # Caught up to the absolute oldest item in FreshRSS.
                break
        log.info(
            "fever items_in_range: walked %d pages, %d items seen, %d in window",
            pages, items_seen, len(out),
        )
        return out


def _parse_item(raw: dict[str, Any]) -> FeverItem:
    return FeverItem(
        id=str(raw.get("id")),
        feed_id=str(raw.get("feed_id", "")),
        title=str(raw.get("title", "")).strip() or "(untitled)",
        author=(str(raw.get("author")).strip() or None) if raw.get("author") else None,
        html=str(raw.get("html", "")),
        url=raw.get("url") or None,
        is_saved=bool(raw.get("is_saved", 0)),
        is_read=bool(raw.get("is_read", 0)),
        created_on_time=int(raw.get("created_on_time", 0)),
    )


def published_iso(item: FeverItem) -> str:
    """Convert Fever's unix `created_on_time` to ISO-8601 UTC, falling
    back to "now" if the field is missing/zero."""
    ts = item.created_on_time
    if ts <= 0:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def html_to_plain_text(html: str, *, max_len: int | None = None) -> str:
    """Strip HTML tags from a Fever item's `html` field. The feed's
    description is what the user (per their workflow) has already
    LLM-synthesised — we just want the readable text out of it.

    `max_len` is now optional — we keep the full body for on-disk
    storage. Pass an explicit cap if you only want a preview."""
    import re

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    if max_len is not None and len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def extract_first_image(html: str) -> str | None:
    """Pull the first `<img src="...">` URL from a Fever item's body.
    Used as a thumbnail in the article-detail pane. Returns None if
    nothing matches."""
    import re

    if not html:
        return None
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    return m.group(1) if m else None
