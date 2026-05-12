# News: Migration from Fever API to FreshRSS Google-Reader API

Study only. **Do not implement from this document yet** — it is intended
as an alignment artefact before any code changes.

- **Date**: 2026-05-12
- **Scope**: replace the Fever-API code path in `backend/app/news/` with
  the FreshRSS-native Google-Reader-compatible API (`/api/greader.php`),
  preserve every feature we already have, and add: add/remove
  categories, add/remove feeds, mark/unmark favourite, add/remove
  labels.
- **Non-goals**: changing the on-disk article JSON format, redesigning
  the three-pane UI, changing cron cadence or retention. Touch those
  only if a feature requires it.

---

## 1. Why move off Fever?

The Fever API is read-mostly: it exposes `feeds`, `groups`, `items`,
`favicons`, `unread_item_ids`, and `mark=item&as=read|unread`. That's
it. It cannot:

- subscribe to a new feed
- unsubscribe from a feed
- create / rename / delete a category (folder)
- mark an item as starred / favourite (FreshRSS *has* a star, but
  Fever never exposed `is_saved` write actions for users)
- add / remove arbitrary labels on an item

FreshRSS's Google-Reader-compatible API at `/api/greader.php` covers
all of that. Migrating swaps a read-only consumption client for one
that can both read and write the user's library.

> Note on the existing `FeverItem.is_saved` field
> (`backend/app/news/fever_client.py:35`, `backend/app/news/fever_client.py:309`):
> we parse it but never persist or display it. It would be discarded
> by the migration in any case — the GReader equivalent is the
> `user/-/state/com.google/starred` category on items.

---

## 2. Current state — what the Fever code does today

### 2.1 Files involved

| Path | Role |
|---|---|
| `backend/app/news/fever_client.py` | Async httpx wrapper over `?api&…` endpoints |
| `backend/app/news/service.py` | Fetch orchestration: incremental + ranged walks, completeness pass, favicons, exclusions |
| `backend/app/news/store.py` | SQLite reads/writes (`news_articles`, `news_feeds`, `news_fetch_runs`) |
| `backend/app/news/articles.py` | On-disk JSON for full article record |
| `backend/app/news/capture.py` | LLM capture flows (unchanged by this migration) |
| `backend/app/api/news.py` | HTTP API exposed to the frontend |
| `backend/app/config.py` (`FreshRSSSourceConfig`, l.189–206) | `base_url` + `api_key` (md5) + `excluded_group_ids` |
| `frontend/src/components/news/NewsView.tsx` | Three-pane UI |
| `frontend/src/lib/api.ts` (l.128–157) | `NewsArticleSummary`, `NewsArticleDetail`, `NewsFeedSummary` |
| `backend/migrations/0010_news_slim.sql` | Current schema (slim `news_articles` + `news_feeds` + `news_fetch_runs`) |

### 2.2 Fever endpoints we currently call

| Method on `FeverClient` | Fever request | Used for |
|---|---|---|
| `feeds()` | `?api&feeds` + `?api&groups` | Build `feed_id → (title, group_name, site_url, favicon_id)` |
| `favicons()` | `?api&favicons` | Data-URI favicons keyed by `favicon_id` |
| `items_since(since_id)` | `?api&items&since_id=…` | Incremental new-items walk (manual fetch + cron incremental) |
| `items_in_range(from_ts,to_ts)` | `?api&items&max_id=…` | 30-day ranged walk (cron full) |
| `unread_item_ids()` | `?api&unread_item_ids` | Reconcile is_read both ways + find missing unread |
| `items_by_ids(ids, ≤50)` | `?api&items&with_ids=…` | Backfill missing unread items |
| `mark_item_read(id)` | `?api&mark=item&as=read&id=…` | Push read flip |
| `mark_item_unread(id)` | `?api&mark=item&as=unread&id=…` | Push unread flip |

### 2.3 HTTP endpoints surfaced by `backend/app/api/news.py`

- `GET  /api/news/feeds` — sidebar with `(feed_id, title, group, favicon, total, unread)`
- `GET  /api/news/articles` — filterable by feed_id / feed_group, optional `unread_only`
- `GET  /api/news/articles/{id}` — header + JSON body
- `POST /api/news/articles/{id}/read` and `…/unread` — flip + push upstream
- `POST /api/news/articles/{id}/keep`, `/article`, `/watched`, `/custom` — LLM capture (no migration impact)
- `POST /api/news/fetch` — manual incremental or ranged
- `GET  /api/news/runs` — fetch-run observability

### 2.4 What's missing today

The user can read, mark read/unread, and capture. They can **not** from
this UI:

1. Add / remove a category (FreshRSS "folder").
2. Add / remove a feed (subscribe / unsubscribe).
3. Star / unstar an article (favourite).
4. Add / remove arbitrary labels on an article.

All four require the GReader API. (1)–(3) and label management are
also FreshRSS-side write operations, so we need both a backend
client and HTTP endpoints + UI to expose them.

---

## 3. Target API — FreshRSS Google-Reader

All endpoints live at `<base>/api/greader.php/reader/api/0/…` and a
small set of un-versioned prefixes (`/accounts/ClientLogin`, `/reader/api/0/token`).
The FreshRSS implementation lives in `p/api/greader.php` — anything we
build should be testable against that source.

### 3.1 Authentication

Two-step:

1. **ClientLogin** — `POST /api/greader.php/accounts/ClientLogin`
   with form params `Email=<user>&Passwd=<password>`. Response is
   three plain-text lines `SID=… / LSID=… / Auth=<token>`. The
   `Auth` token is what we keep. **Validity: ~7 days** — we will
   need to refresh on `401`.
2. **Authorization header** on every read request:
   `Authorization: GoogleLogin auth=<Auth-token>`.
3. **POST CSRF token** for every write request:
   - `GET /reader/api/0/token` with the Authorization header above
   - returns a plain-text token
   - send it on writes as the POST body parameter `T=<token>`
   - validity ~30 min; on `401 X-Reader-Google-Bad-Token: true` we
     refresh and retry once.

This is heavier than Fever's "stash a precomputed md5 in config", so
the config schema must change (see §5.5).

### 3.2 Read endpoints we will use

| Endpoint | Replaces / Adds | Notes |
|---|---|---|
| `GET /reader/api/0/subscription/list?output=json` | Fever `feeds` + `groups` (resolved) | One call. Items: `{id:"feed/<n>", title, htmlUrl, sortid, firstitemmsec, iconUrl, categories:[{id:"user/-/label/<name>", label}]}`. `iconUrl` is a real URL — different from Fever's base64 favicons (see §6.3). |
| `GET /reader/api/0/tag/list?output=json` | (new) | Lists folders **and** user labels. Both come back as `user/-/label/<name>` ids; FreshRSS differentiates internally but the API namespace is shared. |
| `GET /reader/api/0/unread-count?output=json` | (new — optional optimisation) | `{max, unreadcounts:[{id, count, newestItemTimestampUsec}]}` — could replace per-feed COUNT(*) in the sidebar query if we want it server-side. |
| `GET /reader/api/0/stream/contents/<streamId>?output=json` | Fever `items` | `streamId` is URL-encoded. Returns `items[]` (see §3.4). Supports `n=<count>`, `c=<continuation>`, `r=o` (oldest-first), `ot=<ts>`, `nt=<ts>`, `xt=<streamId>`, `it=<streamId>`. |
| `GET /reader/api/0/stream/items/ids?s=<stream>&n=<n>&output=json` | Fever `unread_item_ids` | We hit `s=user/-/state/com.google/reading-list&xt=user/-/state/com.google/read` to enumerate every unread item id without pulling content. |
| `GET /reader/api/0/stream/items/contents?i=<id>&i=<id>…` (or POST) | Fever `with_ids` | Batch fetch full item content by id list. |

### 3.3 Write endpoints we will use

All POSTs include `T=<csrf-token>` in the body and the Authorization
header.

| Endpoint | Operation | Body |
|---|---|---|
| `POST /reader/api/0/subscription/quickadd` | Subscribe by URL | `quickadd=<feed-or-site-url>` — FreshRSS will discover the feed. Returns `{numResults, query, streamId, streamName}`. |
| `POST /reader/api/0/subscription/edit` | Subscribe / rename / move / unsubscribe | `ac=subscribe\|edit\|unsubscribe`, `s=feed/<id>`, optional `t=<title>`, `a=user/-/label/<folder>` (add to folder, creates it if missing), `r=user/-/label/<folder>` (remove). |
| `POST /reader/api/0/rename-tag` | Rename a category / label | `s=user/-/label/<old>` (or `t=<old>`), `dest=user/-/label/<new>`. |
| `POST /reader/api/0/disable-tag` | Delete a category / label | `s=user/-/label/<name>` (or `t=<name>`). **Caveat — FreshRSS support: not present in the source we read**. Verify on the target FreshRSS instance; if not implemented we fall back to "remove the label from every member feed" (categories auto-vanish when empty in FreshRSS). See §8.1. |
| `POST /reader/api/0/edit-tag` | Toggle item state / labels | Repeatable `i=<itemId>`, repeatable `a=<tag>` and/or `r=<tag>`. Tags: `user/-/state/com.google/read` (read), `user/-/state/com.google/starred` (favourite), `user/-/label/<name>` (label). **There is no positive `kept-unread` in FreshRSS** — to mark an item unread, send `r=user/-/state/com.google/read`. |
| `POST /reader/api/0/mark-all-as-read` | Bulk read | `s=<streamId>`, optional `ts=<μs>` (only items older than ts). Useful for a "mark all as read" UI action on a feed/category. |

### 3.4 Item shape (relevant fields only)

```json
{
  "id": "tag:google.com,2005:reader/item/0000000000abcdef",
  "timestampUsec": "1715000000000000",
  "crawlTimeMsec": "1715000000000",
  "published": 1715000000,
  "updated": 1715000000,
  "title": "…",
  "author": "…",
  "summary":   { "content": "<html>…</html>" },
  "content":   { "content": "<html>…</html>" },
  "alternate": [ { "href": "https://publisher/post", "type": "text/html" } ],
  "origin":    { "streamId": "feed/42", "title": "Feed name", "htmlUrl": "https://publisher" },
  "categories": [
    "user/1/state/com.google/reading-list",
    "user/1/state/com.google/read",
    "user/1/state/com.google/starred",
    "user/1/label/Tech"
  ]
}
```

Key mappings:

- `is_read` = `categories` contains `user/<n>/state/com.google/read`.
- `is_starred` = `categories` contains `user/<n>/state/com.google/starred`.
- `labels` = every `user/<n>/label/<name>` entry whose `<name>` is not also a folder of the item's owning feed. FreshRSS overloads `user/-/label/<name>` for both folders and labels — to tell them apart on inbound items, intersect with the set returned by `subscription/list` (those are folder names) versus the user's labels (which we can fetch via `tag/list` and filter to label-type ids).

> **Item-id format**: long form `tag:google.com,2005:reader/item/<hex>`,
> short form `<decimal>` accepted by `edit-tag`. We store the canonical
> hex string in `external_id` so the existing `(source, external_id)`
> unique constraint keeps working — see §5.1.

---

## 4. Feature matrix: current vs target

| Feature | Fever today | GReader after migration |
|---|---|---|
| List feeds with group & favicon | `feeds()` + `groups()` + `favicons()` | `subscription/list` (one call). Favicon via `iconUrl` (we fetch + cache; see §6.3). |
| Incremental fetch of new items | `items_since(since_id)` | `stream/contents/user/-/state/com.google/reading-list?nt=<since_unix>&r=o` (oldest-first since timestamp) |
| 30-day ranged fetch | `items_in_range(from_ts,to_ts)` | `stream/contents/.../reading-list?ot=<from>&nt=<to>` with pagination via `c=` |
| Unread completeness | `unread_item_ids()` + `items_by_ids()` | `stream/items/ids?s=…/reading-list&xt=…/read&n=10000` then `stream/items/contents?i=…` for missing |
| Mark read / unread | `mark=item&as=read\|unread&id=…` | `edit-tag` with `a=` or `r=` of `user/-/state/com.google/read` |
| **Add a category** | ❌ | Implicit: pass `a=user/-/label/<new>` on `subscription/edit ac=edit\|subscribe`. To create a *standalone* empty folder we can post a no-op `subscription/edit` on an existing feed and immediately undo, OR (simpler UX) hide the "create empty category" affordance and create-on-first-feed-added. **Recommended**: UI offers "new category" only as part of the add-feed flow. |
| **Remove a category** | ❌ | `disable-tag s=user/-/label/<name>` *if FreshRSS exposes it*; otherwise iterate over feeds in the category and `subscription/edit ac=edit r=user/-/label/<name>` for each. |
| **Add a feed** | ❌ | `subscription/quickadd?quickadd=<url>` (then optionally `subscription/edit a=user/-/label/<cat>` to file it). |
| **Remove a feed** | ❌ | `subscription/edit ac=unsubscribe s=feed/<id>`. |
| **Mark favourite / unmark** | ❌ | `edit-tag a=user/-/state/com.google/starred` / `r=…/starred` |
| **Add / remove label** | ❌ | `edit-tag a=user/-/label/<name>` / `r=user/-/label/<name>` |
| Rename a category | ❌ | `rename-tag s=user/-/label/<old> dest=user/-/label/<new>` (bonus, ~free) |

---

## 5. Implementation plan

### 5.1 Schema changes

Add **migration 0015_news_greader.sql** (new file). The migration:

```sql
-- 0015_news_greader.sql
-- Track starred + labels locally so the UI doesn't have to round-trip
-- to FreshRSS to render them, and so they survive offline reads.

ALTER TABLE news_articles
    ADD COLUMN is_starred INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_news_articles_starred
    ON news_articles(is_starred) WHERE is_starred = 1;

CREATE TABLE IF NOT EXISTS news_article_labels (
    article_id  TEXT NOT NULL,
    label       TEXT NOT NULL,
    PRIMARY KEY (article_id, label),
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_news_article_labels_label
    ON news_article_labels(label);

-- A separate index of every label the user has ever attached, so the
-- UI can autocomplete without scanning news_article_labels.
CREATE TABLE IF NOT EXISTS news_labels (
    name        TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);
```

We deliberately keep labels in a side-table rather than a JSON column
on `news_articles` so the article row stays SQLite-friendly (small,
indexable) and "all articles with label X" is a single indexed lookup.
This matches the existing "no ORM, raw SQL only" stance.

Categories do **not** need a local table — they're a property of the
*feed* (`news_feeds.feed_group`), already stored.

The `external_id` column keeps holding the GReader hex item id as a
string. Fever returned decimal ids (microsecond timestamps stringified);
GReader returns the same numeric ids but in 16-char zero-padded hex.
We canonicalise to hex on ingest; existing rows from Fever are
preserved because the schema is idempotent on the column type, but
**read-state reconciliation across the migration boundary needs
care** — see §7.

### 5.2 Backend: replace `FeverClient` with `GReaderClient`

New module `backend/app/news/greader_client.py`. Mirror the
`FeverClient` constructor/context-manager pattern so the rest of
`service.py` only changes by a one-line client-class swap.

Public surface (proposed):

```python
class GReaderClient:
    def __init__(self, *, base_url: str, username: str, password: str) -> None: ...

    async def __aenter__(self) -> "GReaderClient": ...
    async def __aexit__(self, *_): ...

    # --- read ---
    async def subscriptions(self) -> dict[str, GReaderFeed]: ...
    async def tags(self) -> list[GReaderTag]: ...
    async def unread_counts(self) -> dict[str, int]: ...   # streamId → count
    async def items_since(self, *, since_ts: int, max_items: int = 500) -> list[GReaderItem]: ...
    async def items_in_range(self, *, from_ts: int, to_ts: int | None) -> list[GReaderItem]: ...
    async def unread_item_ids(self, *, max_ids: int = 10_000) -> list[str]: ...
    async def items_by_ids(self, ids: list[str], *, batch_size: int = 250) -> list[GReaderItem]: ...

    # --- write: items ---
    async def mark_item(self, item_id: str, *, add: list[str] = (), remove: list[str] = ()) -> None: ...
    # convenience wrappers built on top:
    async def mark_read(self, item_id: str) -> None: ...
    async def mark_unread(self, item_id: str) -> None: ...
    async def mark_starred(self, item_id: str, *, starred: bool) -> None: ...
    async def add_label(self, item_id: str, label: str) -> None: ...
    async def remove_label(self, item_id: str, label: str) -> None: ...

    # --- write: feeds & categories ---
    async def subscribe(self, url: str, *, title: str | None = None, category: str | None = None) -> str: ...   # returns "feed/<id>"
    async def unsubscribe(self, feed_id: str) -> None: ...
    async def move_feed_to_category(self, feed_id: str, *, category: str | None) -> None: ...
    async def rename_category(self, old: str, new: str) -> None: ...
    async def delete_category(self, name: str) -> None: ...
    async def mark_stream_all_read(self, stream_id: str, *, older_than_us: int | None = None) -> None: ...
```

Internals:

- Single `httpx.AsyncClient`. Holds `Auth` token and a cached CSRF
  `T` token. Both refresh lazily on `401`. One retry per call on
  token failure.
- All `i=` ids passed in are normalised to short decimal form before
  the request — FreshRSS accepts both forms and decimal keeps URLs
  shorter for batch requests.
- All POST writes share a small `_post_form(path, params)` helper
  that injects `T=<csrf>` and the Authorization header.

### 5.3 Service-layer changes (`backend/app/news/service.py`)

- `fetch_freshrss` keeps its shape. It just calls the new client.
- Two adjustments to the ingest path:
  - On each ingested item, derive `is_starred` from `categories` and
    `labels` from category strings that look like `user/<n>/label/<x>`
    minus folder names; pass into `insert_article` / new
    `replace_article_labels` helper.
  - Favicon flow changes from "base64 data URI from `?api&favicons`"
    to "fetch the `iconUrl` once per feed, base64 it, store in
    `news_feeds.favicon_data_uri`" — see §6.3. Done at `upsert_feed`
    call site, gated on "missing or older than N days".
- New helpers on top of the existing `service.py` for write paths:
  - `push_read_state(article_id, source, external_id, is_read)` — same
    signature as today, swaps the underlying call.
  - `push_starred_state(article_id, …, is_starred)` — new.
  - `push_label(article_id, …, label, *, add: bool)` — new.
  - `subscribe_feed(url, *, category)`, `unsubscribe_feed(feed_id)`,
    `move_feed(feed_id, *, category)` — new.
  - `rename_category(old, new)`, `delete_category(name)` — new.
- Reconciliation pass: the existing two-way `reconcile_read_state`
  still works — drive it from `stream/items/ids?...&xt=...&read` (the
  set of *unread* ids on the server). Add a sibling
  `reconcile_starred_state(conn, *, source, starred_external_ids)`
  driven from `stream/items/ids?s=…/starred`. Skip on transient API
  errors, same as today.

### 5.4 HTTP API additions (`backend/app/api/news.py`)

Keep every existing endpoint with **identical request/response shape**
where possible (the UI keeps working). Add:

| Method | Path | Body / Query | Effect |
|---|---|---|---|
| `POST` | `/api/news/articles/{id}/star` | — | Star upstream + local flip |
| `POST` | `/api/news/articles/{id}/unstar` | — | Unstar |
| `POST` | `/api/news/articles/{id}/labels` | `{label: str}` | Add label (also writes to `news_labels`) |
| `DELETE` | `/api/news/articles/{id}/labels/{label}` | — | Remove label |
| `GET` | `/api/news/labels` | — | List `news_labels` |
| `GET` | `/api/news/categories` | — | List distinct `news_feeds.feed_group` |
| `POST` | `/api/news/feeds` | `{url: str, title?: str, category?: str}` | Subscribe (`subscription/quickadd` + optional `subscription/edit a=`) |
| `DELETE` | `/api/news/feeds/{feed_id}` | — | Unsubscribe |
| `PATCH` | `/api/news/feeds/{feed_id}` | `{title?: str, category?: str}` | Rename / re-file |
| `POST` | `/api/news/categories` | `{name: str, feed_ids?: [str]}` | "Create category" by moving the listed feeds into a fresh label name (FreshRSS creates it lazily) |
| `PATCH` | `/api/news/categories/{old}` | `{name: str}` | Rename via `rename-tag` |
| `DELETE` | `/api/news/categories/{name}` | — | `disable-tag` if available, else fan-out remove-label across member feeds (§3.3) |

Extensions to existing DTOs:

- `ArticleSummaryDTO` and `ArticleDetailDTO` gain `is_starred: bool`
  and `labels: list[str]`.
- `NewsFeedSummary` (frontend type) is unchanged — categories already
  ship via `feed_group`.

Pattern reused: each write endpoint flips local state synchronously,
then `asyncio.create_task(_push())` to fire the upstream call, like
the current read/unread flow at `backend/app/api/news.py:230-246`.
That keeps the UI snappy and survives transient FreshRSS hiccups.

### 5.5 Configuration changes

`FreshRSSSourceConfig` in `backend/app/config.py:189-206` is the
breaking change.

**Before** (`api_key` = `md5(user:pass)` only):

```yaml
news.sources.freshrss:
  base_url: "https://freshrss.example.com/api/fever.php"
  api_key: "REPLACE_WITH_MD5_HASH"
  max_items_per_run: 500
  excluded_group_ids: []
```

**After**:

```yaml
news.sources.freshrss:
  base_url: "https://freshrss.example.com"   # no longer ends in /api/fever.php
  username: "alice"
  password: "<api-password>"                 # FreshRSS supports a per-API password
  max_items_per_run: 500
  excluded_group_ids: []                     # still by feed-group *name* OR id; see §6.1
```

`base_url` becomes the FreshRSS root; the client appends
`/api/greader.php/...`. The `api_key` field is retired. The example
config (`config.example.yml:148-167`) and the CLAUDE rule about no
ORM/raw SQL are both untouched — this is config + Python only.

> The user is expected to set a *dedicated API password* in FreshRSS
> (Settings → Authentication → API password). The migration doc
> README should call this out. The plaintext password lives in
> `config.yml`, same threat-model as the existing md5 hash.

### 5.6 Frontend changes (`frontend/src/components/news/NewsView.tsx` + `lib/api.ts`)

Additive only — none of the existing affordances change behaviour.

1. `lib/api.ts` types:
   - `NewsArticleSummary` / `NewsArticleDetail` gain `is_starred: boolean`, `labels: string[]`.
   - New types `NewsLabel { name: string }`, `NewsFeedCreate { url, title?, category? }`.
2. NewsView additions:
   - **Star button** in the detail toolbar next to read/unread.
   - **Labels chip row** in the detail header; `+` to add (autocompletes
     against `/api/news/labels`); `×` to remove.
   - **"Manage feeds" modal** (gear icon in the feeds sidebar header)
     listing every feed with rename / move-to-category / unsubscribe;
     a `+ Add feed` row at the top (URL + optional category dropdown).
   - **"New category"** menu item in the same modal (inline rename,
     delete with confirm).
   - Sidebar filter: a new "Starred" pseudo-feed at the top of the
     sidebar, plus a per-label section (collapsed by default).
3. React-Query: each write goes through the existing mutation
   pattern (`onSuccess` invalidates `news-feeds` / `news-articles` /
   `news-article` keys).

UI work is the bulk of the diff. The migration can ship in two phases
(see §9) — backend swap first, UI additions second — without breaking
anything in between.

---

## 6. Open / verification points

### 6.1 Categories: by id or by name?

Fever's `excluded_group_ids` is a list of FreshRSS folder ids. GReader
exposes folders by **name** (`user/-/label/<name>`), not numeric id.
Two options:

- Keep ids in config but resolve `id → name` once at startup via
  FreshRSS's web UI / OPML — **fragile**, name renames break it.
- Switch the config field to **names** (`excluded_groups: [str]`),
  migrate the user's existing config manually.

Recommendation: switch to names. Document the rename in the changelog.

### 6.2 Folders vs labels collision

GReader puts both folders and labels under `user/-/label/<name>`. In
FreshRSS, folders are exclusive (a feed has 0–1 folder) and labels
are non-exclusive (an item has 0..n labels). The `tag/list` response
includes both — we will need to discriminate either by:

- intersecting with `subscription/list` (every category id that
  appears there is a folder), or
- looking for the `type` field if FreshRSS returns one (verify against
  the live instance — pre-migration spike).

If discrimination is unreliable, fall back to: "labels live under a
fixed prefix the UI controls" (e.g. `user/-/label/lbl:<name>`). Less
clean, fully deterministic.

### 6.3 Favicons

Fever's `?api&favicons` returns inline base64. GReader's
`subscription/list` returns `iconUrl` — a URL we have to fetch. Plan:

- On `upsert_feed`, if the stored `favicon_data_uri` is missing or
  older than 30 days, fetch `iconUrl`, cap at the existing
  `_FAVICON_MAX_BYTES = 52224` limit (`backend/app/news/service.py:65`),
  base64-encode, store. Reuse `_normalise_favicon`.
- Wrap in try/except — favicons remain non-fatal exactly like today.

### 6.4 `disable-tag` availability

The FreshRSS source we inspected does not expose `disable-tag`. Two
mitigations:

- At startup, probe with a no-op call and feature-flag accordingly.
- If absent, "delete category" iterates over `subscription/list`,
  finds member feeds, and removes the label via `subscription/edit
  r=user/-/label/<name>` for each. FreshRSS auto-prunes empty
  categories — verify on the target instance.

### 6.5 Item-id continuity across the migration

Fever's item ids and GReader's item ids on the same FreshRSS instance
**are the same numeric ids in different encodings** (decimal vs
zero-padded hex). To keep the existing rows in `news_articles`
addressable post-migration:

- At first start under the new client, run a one-shot pass that
  re-encodes every `external_id` to the GReader hex form. This is
  pure SQL (`UPDATE news_articles SET external_id = printf('%016x', CAST(external_id AS INTEGER)) WHERE source = 'freshrss'`),
  followed by the same on `id` (`source:external_id`) and a rename of
  any on-disk JSON files via `articles._path_for`.
- Or, simpler: tolerate both forms in the client. The
  `_unread_ids` reconciliation joins on `external_id`, so if we keep
  ids in their original Fever (decimal) form for already-stored rows
  and write new rows with hex, reconciliation will silently
  mis-classify every old article. **Recommended: do the one-shot
  re-encode.** It's a single transactional SQL block and a `rename` per
  JSON file, guarded by a marker in `news_fetch_runs` so it never runs
  twice.

### 6.6 Pagination defaults

GReader's `stream/contents` default `n=` is small (~20 in
the FeedHQ reference; FreshRSS doesn't enforce a hard cap). For
parity with Fever's 50-per-page walks we'll explicitly pass
`n=100` (still polite, halves request count) and walk
`continuation`. The 30-day ranged walk's safety caps
(`max_pages=1000`, `max_items=100_000`) port over unchanged.

---

## 7. Migration steps (ordered)

Each step is a self-contained commit that should also be pushed
(per the user's standing rule). Each is reversible by reverting the
commit *up to and including* the schema migration.

1. **Spike & verify** the unknowns in §6 against the live FreshRSS
   instance (one-off script, not committed). Confirm:
   - `disable-tag` works (or doesn't).
   - `tag/list` returns enough metadata to tell folder vs label.
   - `subscription/quickadd` accepts both feed and site URLs.
2. **Add `GReaderClient` next to `FeverClient`** (no callers yet).
   Unit-test the auth dance, item-id encoding, batched
   `stream/items/contents`, and edit-tag payloads against a recorded
   FreshRSS instance.
3. **Migration 0015** (schema additions per §5.1) +
   the one-shot `external_id` re-encode (§6.5). Idempotent.
4. **Config schema swap** (§5.5) with a clear startup error if
   `api_key` is present but new fields aren't. Update
   `config.example.yml`.
5. **Switch `service.py` to `GReaderClient`** and adapt favicon
   handling (§6.3). At this point: feature parity with Fever, no new
   UI yet. Run the cron once, verify counts unchanged.
6. **Delete `FeverClient`** (`backend/app/news/fever_client.py`) and
   the now-unused `FeverItem` / `FeverFeed` types. Tests that
   referenced them get rewritten or deleted.
7. **Add write endpoints** in `backend/app/api/news.py` (§5.4)
   alongside service-layer helpers (§5.3). API-only commit, no UI.
8. **Frontend additions** (§5.6) — separate PR, easier to review.
   Star + labels first, then the manage-feeds modal, then the
   manage-categories affordance.
9. **Documentation pass**: README and config.example.yml updated
   (section already has FreshRSS pointers — refresh prose).

---

## 8. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| FreshRSS instance lacks `disable-tag` | medium | medium | Feature-detect at startup; fall back to per-feed label removal (§6.4). |
| Folder/label ambiguity causes mis-rendered chips | medium | low | Discriminate via `subscription/list` membership; document fallback prefix (§6.2). |
| Auth token churn (7d) silently fails cron | low | high | Catch 401 on every request, ClientLogin → retry once. Log auth refreshes at INFO. |
| CSRF token churn during burst writes (label spam) | low | low | Same retry-once-on-401 pattern; refresh `T` lazily. |
| Item-id re-encode runs twice on an already-migrated DB | low | high (data corruption) | Idempotent guard: insert a sentinel row in `news_fetch_runs` (`kind='migration'`); skip if present. |
| Favicon fetch hammers third-party sites on first run after migration | low | low | Cap concurrency to ~4, only refetch when older than 30d, hard-fail silently. |
| Backwards compat: old config files break startup | medium | low | Pydantic validator: if legacy `api_key` is set and `username`/`password` are absent, raise a clear error pointing at the migration doc. |
| Manual fetch races subscribe/unsubscribe (feed appears/disappears mid-walk) | low | low | `INSERT OR IGNORE` already absorbs duplicates; `subscription/list` is re-fetched at the top of every run. |

---

## 9. Phased rollout

- **Phase 1 (backend swap, no new features)** — steps 1–6. The user's
  experience is identical, but the codebase is on the GReader API.
  Verifies that we can read everything we used to read.
- **Phase 2 (write endpoints)** — step 7. Backend can subscribe,
  star, label, manage categories, but the UI doesn't expose it yet.
  Testable via `curl` / OpenAPI docs.
- **Phase 3 (UI)** — steps 8–9. End-user-visible.

If Phase 1 surfaces an unexpected GReader behaviour on the user's
FreshRSS instance, we revert the single commit that flipped the
client and remain on Fever indefinitely. Phase 2 and Phase 3 commits
are additive — they can sit unreverted even if Phase 1 is rolled
back, as long as their endpoints fail closed.

---

## 10. Out of scope (do not bundle into this migration)

- Changing `news_articles` retention rules or the JSON-on-disk format.
- Adding multi-source ingest (e.g. Miniflux, Tiny Tiny RSS) — the
  abstraction is currently 1:1 with FreshRSS; generalising can come
  later.
- Server-side full-text search.
- Push notifications on starred articles.
- Importing / exporting OPML (FreshRSS already handles that natively
  in its own UI).

---

## 11. Checklist before starting implementation

- [ ] Spike script confirms §6 unknowns on the user's FreshRSS host.
- [ ] User generates an API password in FreshRSS and adds it to
      `config.yml` alongside the existing setup (no need to remove
      the md5 yet — migration step 4 does that).
- [ ] Confirm the user's existing categories vs labels — if the user
      *also* uses FreshRSS labels separately from folders today, §6.2
      is load-bearing and we need to nail discrimination before
      writing the UI.
- [ ] Confirm whether to keep `excluded_group_ids` as ids or switch
      to names (§6.1).
