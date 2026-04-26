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

    async def feeds(self) -> dict[str, FeverFeed]:
        """Return feed_id → FeverFeed for the configured user."""
        body = await self._post(params={"feeds": ""})
        out: dict[str, FeverFeed] = {}
        for raw in body.get("feeds", []) or []:
            fid = str(raw.get("id"))
            out[fid] = FeverFeed(
                id=fid,
                title=str(raw.get("title", "")) or fid,
                site_url=raw.get("site_url") or None,
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
    ) -> list[FeverItem]:
        """Walk items newest-first via Fever's `max_id` cursor and return
        those whose `created_on_time` falls in [from_ts, to_ts]. Stops
        early once a page contains items strictly older than `from_ts`
        (Fever returns ids in monotonically-decreasing order with max_id,
        and item id correlates with created_on_time, so once we see an
        out-of-range item we know the rest of the walk is also out of
        range).

        `to_ts=None` means "no upper bound" (i.e. up to now). Returned
        items are newest-first."""
        out: list[FeverItem] = []
        # FreshRSS's Fever endpoint returns no items when `items` is
        # called without a filter (since_id / max_id / with_ids). Seed
        # the cursor with a value larger than any plausible item id so
        # the first page returns the absolute newest items.
        #
        # NB: FreshRSS uses microsecond-timestamp item ids (~1.78e15 for
        # 2026 articles), not sequential integers, so a 32-bit ceiling
        # (~2.1e9) is far too small — FreshRSS would return "no items
        # older than ~1970" and we'd silently get nothing. 2^63 - 1 sits
        # above any plausible timestamp-derived id for centuries.
        cursor: int = 2**63 - 1
        # Same page cap as items_since — Fever pages are 50 items.
        max_pages = max(1, (max_items + 49) // 50)
        for _ in range(max_pages):
            body = await self._post(
                params={"items": "", "max_id": str(cursor)}
            )
            items = body.get("items") or []
            if not items:
                break
            below_floor = False
            for raw in items:
                item = _parse_item(raw)
                ts = item.created_on_time
                # Track the cursor for the next page regardless of
                # whether this item is in-range — we need to keep walking
                # backwards through items to find the boundary.
                rid = int(raw.get("id", 0))
                if rid < cursor:
                    cursor = rid
                if ts and ts < from_ts:
                    below_floor = True
                    continue
                if to_ts is not None and ts and ts > to_ts:
                    continue
                out.append(item)
                if len(out) >= max_items:
                    return out
            if below_floor:
                # We crossed the lower bound on this page — anything older
                # is by definition out of range.
                break
            if len(items) < 50:
                break
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


def html_to_plain_text(html: str, *, max_len: int = 1200) -> str:
    """Strip HTML tags from a Fever item's `html` field. The feed's
    description is what the user (per their workflow) has already
    LLM-synthesised — we just want the readable text out of it."""
    import re

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text
