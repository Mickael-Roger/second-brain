# Anki integration

Second Brain's server is the bridge to AnkiConnect. The web app, installed PWA
and standalone TUI use the same authenticated `/api/anki` routes. No Anki
collection or review scheduling state is mirrored in Second Brain.

## Configuration

Install AnkiConnect (add-on `2055492159`) in Anki desktop and keep Anki running.
Configure a URL reachable **from the Second Brain server**, not from the browser
or phone:

```yaml
anki:
  enabled: true
  url: http://host.docker.internal:8765
  api_key: null
  timeout_seconds: 30
```

For a server running directly on the same machine as Anki, use
`http://127.0.0.1:8765`. For Docker, the example Compose file already defines
`host.docker.internal`; configure AnkiConnect's listening address to accept
connections from the container. Set `api_key` to AnkiConnect's configured key
when applicable. Only the backend connects to AnkiConnect; mobile clients do
not need direct access to the desktop.

## Client workflow

1. Open Anki to see every deck and its counters, including empty decks.
2. Expand/collapse a deck to show/hide its due and new cards directly beneath it.
   Multiple decks can stay expanded. Suspended/buried cards are excluded.
3. Select a card to open a review popup over the same deck list. Its rendered
   question is shown first, respecting reversed and cloze templates. Reveal the
   answer, then choose Again, Hard, Good or Easy.
   Anki's estimated next review interval is displayed below each rating. The
   values come directly from `cardsInfo.nextReviews`, in Again/Hard/Good/Easy
   order. Missing or ambiguous interval data is displayed as `—`.
4. A successful rating closes the popup and refreshes cards/counters without
   resetting expanded decks. Close or Escape dismisses the popup without rating.
   A failed request preserves the popup and answer for retry; mutations are not
   retried automatically.
5. Use New note (`c` outside text inputs) to open a creation popup. Choose a deck,
   front/back, normal/reversed mode and optional space-separated tags.

The web/PWA renders card HTML/CSS in an isolated reading pane. Collection media
images and audio use the authenticated media proxy. The TUI converts card HTML
to Markdown and displays supported images in a portable media pane inside the
review popup. In the TUI, Tab/Shift+Tab moves between popup controls; Escape on
the list collapses the selected deck.

The PWA uses the same live API as the browser. The application shell is cached,
but Anki data and review mutations are not cached or queued by the service
worker. Offline status is shown and saving is disabled until connectivity returns.

### Counter semantics

Counters are Anki **card** counts, not note counts. A reversed note creates two
cards. `new_count`, `learn_count` and `review_count` come from Anki's scheduler
and include subdecks; they can be affected by daily limits. `total_in_deck` is
the deck's total card count. Older Anki versions without this total are handled
by a card search. Empty decks omitted by AnkiConnect are verified with a search
and shown with zero counts; other missing statistics produce an error.

The due/new list is a browser search, not Anki's exact scheduler queue: it can
contain more new cards than the scheduler's daily allowance. Future learning
steps do not appear until due. Web/PWA starts with 100 cards and offers Load more
(up to 10,000 per deck); TUI loads expanded decks in pages of 1,000 cards.
In the TUI, `u` / `:cards` switches to read-only browsing of all cards, including
those not due, while retaining expanded decks and the local search filter.

## Server API and AnkiConnect mapping

All routes require the normal Second Brain session. Operations other than
`/config` also require `anki.enabled`. Plugin/network failures return HTTP 502;
disabled integration returns 403, and missing cards/media return 404.

| Method and route | AnkiConnect actions |
| --- | --- |
| `GET /api/anki/config` | Local enabled flag |
| `POST /api/anki/sync` | `sync` (desktop collection ↔ AnkiWeb) |
| `GET /api/anki/decks` | `deckNamesAndIds` (legacy names/IDs list) |
| `GET /api/anki/deck-stats` | `deckNamesAndIds`, `getDeckStats`; `findCards` for missing totals |
| `GET /api/anki/due?deck=AI` | `findCards`, `cardsInfo` |
| `GET /api/anki/cards?deck=AI` | `findCards` (all deck cards), `cardsInfo` |
| `POST /api/anki/cards/{card_id}/answer` | `answerCards` |
| `POST /api/anki/cards` | `addNote` |
| `GET /api/anki/media?filename=picture.png` | `retrieveMediaFile` |

`/deck-stats` returns an array of objects with `deck_id`, `name`, `new_count`,
`learn_count`, `review_count`, and `total_in_deck`.

`/due` returns an array of card objects: `card_id`, `note_id`, `deck_name`,
`model_name`, `front`, `back`, rendered `question`/`answer`, `css`, `state`, `due`,
`interval`, `next_reviews` (four display strings, or an empty list when unavailable),
and `tags`. Query parameters: `deck` (required), `include_new`
(default true), `limit` (1–10,000; default 1,000), `offset` (default 0).

`GET /cards` returns the same card objects, without a due/new filter. Required
parameter: `deck`; pagination: `limit` (1–10,000; default 1,000), `offset` (default 0).
It includes future, suspended and buried cards and does not change their schedule.

`/cards/{card_id}/answer` takes `{"ease": 3}`: 1 Again, 2 Hard, 3 Good, 4 Easy.
It returns `{"answered": true}` only when AnkiConnect accepts the answer.

`POST /cards` takes `deck`, `front`, `back`, `mode` (`normal` or `reverse`) and
`tags`. It returns `note_id` and `cards_created` (1 or 2).

### AnkiWeb synchronization

AnkiConnect reads the collection open on its host desktop, not the collection on
a phone. Use **Sync AnkiWeb** in the web/PWA or **`s` / `:sync`** on the TUI's
Anki page to synchronize the host desktop with AnkiWeb. The backend awaits
AnkiConnect's `sync` action and returns `{"synced": true}` on success. Deck
statistics and expanded card lists then reload without collapsing decks.

The open desktop profile must be connected to the correct AnkiWeb account.
Synchronize the phone/source device first so its changes are available on
AnkiWeb. Sync errors (including missing AnkiWeb authentication) are displayed
and can be retried manually. Sync is not triggered automatically on page load,
ordinary refresh, or offline reconnection; failed syncs are not retried or queued.

If AnkiConnect reports `Sync status 2 not one of [0, 1]`, Anki requires a **full
sync**, not an ordinary incremental sync. Second Brain returns HTTP 409 with
desktop instructions. Open Anki on the AnkiConnect host and synchronize there:
Anki asks whether to Upload the local collection or Download the AnkiWeb
collection. If AnkiWeb contains the up-to-date collection, choose Download.
Then retry Sync AnkiWeb in Second Brain. Status 3 specifically requires a full
Download and status 4 a full Upload. These operations are resolved in Anki's
desktop interface, rather than selected automatically by Second Brain.

## Deployment

Deploy the backend and built frontend together. Restarting an old server binary
does not add the new routes. For the example Docker Compose deployment:

```bash
docker compose up -d --build second-brain
```

The Docker build includes the web/PWA assets. For a source deployment, run
`npm ci` and `npm run build` in `frontend/`, serve the resulting `frontend/dist`
with the updated backend, and restart the backend service. Its built-in static
directory is `backend/app/static`; the Docker/Nix builds populate that directory.
For Nix, rebuild/redeploy the server derivation, which embeds the frontend.

In an installed PWA, accept the app update/reload prompt so the new UI is active.
Rebuild the TUI with `go -C tui build -o second-brain-tui .`.

If only deck names appear, use `second-brain-tui --debug SERVER_URL` and inspect
the trace after quitting. A 404 on `/api/anki/deck-stats` or `/api/anki/due` means
the deployed API does not expose the new routes. The legacy TUI fallback shows
`—` for unavailable counts rather than reporting them as zero.

## Verification

`uv run pytest` covers the API contracts, authentication/disabled gating,
statistics fallback and actual HTTP request envelopes to a mocked AnkiConnect.
`npm run build` in `frontend/` checks TypeScript and generates the production PWA.
`go -C tui test ./...` and `go -C tui vet ./...` cover terminal workflows.
