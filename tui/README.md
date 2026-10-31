# Second Brain — Go terminal client

A standalone Go application using tview/tcell. It connects to an existing
Second Brain server through its authenticated HTTP API and SSE streams.

## Build and run

Go **1.26+** is required to build. From the repository root:

```bash
go -C tui build -o second-brain-tui .
./tui/second-brain-tui https://brain.example.com --username mickael --images iterm2
```

Or run from source:

```bash
go -C tui run . --images auto https://brain.example.com
```

The URL is required. Options can precede or follow it. `--help` lists the
available options. URL path prefixes, such as `https://example.com/brain`,
are preserved when constructing API requests.

Copy the compiled binary to a machine with the same OS/architecture to use it
without Go, Python, or any other runtime. A static Linux build is available via:

```bash
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go -C tui build -trimpath -o second-brain-tui .
```

Sign in with the same username/password as the web app. Credentials and cookies
are kept only in memory. The client does not load server configuration or
access a local vault/database. It uses standard TLS certificate verification.

## Navigation

The interface follows a k9s-style resource workflow: a black background, a compact
keyboard-reference dashboard, and a full-screen hierarchical table. The header
shows global and context-specific actions on the left, numeric page shortcuts on
the right, and a yellow Second Brain logo. Tables have fixed cyan column headings,
a blue selected row, thin borders, and group counters. News separates the title,
feed, date, and unread/total counts into columns. The bottom line shows the current
view and selection breadcrumb.

Press a page number to switch directly, exactly like entering its command:

| Header key | Page | Equivalent command |
| --- | --- | --- |
| `<0>` | Chat | `:chat` |
| `<1>` | Anki | `:anki` |
| `<2>` | Training | `:training` |
| `<3>` | News | `:news` |

The active page is highlighted. Numbers remain ordinary text inside text inputs,
editors, dialogs, and the command/search prompt. Press `?` for help and `r` to
refresh the current view.
Each section starts on its list with groups collapsed. Use Up / Down to select
a row and Enter to expand/collapse a group or open a leaf inside the terminal.
Escape returns to the list, preserving expansion, selection and search.

Commands and filters share one prompt below the header, following
[k9s command mode](https://k9scli.io/topics/commands/). The prompt is a single
borderless line below the keyboard dashboard. Press `:` and enter a command, for example `:chat`; Tab completes
command names and Up / Down navigates suggestions. A command can also include
a filter: `:news /security` opens News with that filter already applied.

Press `/` in the same prompt for live fuzzy
search: ordered characters need not be adjacent, and space-separated terms can
match different parts of the title, parent path or metadata. Matches are ranked
and highlighted, including leaves inside collapsed groups. Up / Down selects a
result while typing. Enter accepts the filter and returns focus to the list;
another Enter opens the selected leaf. Escape cancels prompt changes and restores
the previously accepted filter. Escape on the list clears its filter. Search runs
locally on the loaded items.

Within content views, Tab / Shift+Tab moves between controls. Text areas support
multiline editing, undo/redo and bracketed paste. Command/search keys are treated
as ordinary text while a text input or editor has focus; Escape returns to list
navigation.

| Shortcut | Action |
| --- | --- |
| `0`, `1`, `2`, `3` | Chat, Anki, Training, News |
| `?` | Show keyboard help |
| `r` | Refresh current section |
| `:chat`, `:training`, `:news`, `:anki` | Switch section |
| `:news /text` (or another section) | Switch section and apply a fuzzy filter |
| Tab in command mode | Complete a command name |
| `/` | Live ranked fuzzy search across all loaded leaves |
| Up / Down | Select a row or search result |
| Enter | Expand/collapse group or open content |
| Escape | Back to list; clear filter when already on the list |
| `c`, `:new` | New chat or new Anki note, depending on the section |
| `:plan`, `:generate` | Start a learning plan or open fiche generation |
| `:stop` | Stop chat streaming |
| `:fetch`, `:feeds` | Fetch RSS or manage feeds |
| `:refresh` | Refresh current section |
| `:logout` | Sign out |
| `:help` | List commands |
| Ctrl+G | Send chat message |
| Ctrl+L | Redraw native image |
| Ctrl+C, then Ctrl+C within 3 seconds | Quit after confirmation |
| `:q`, `:quit` | Quit directly |

The first Ctrl+C displays a confirmation message without stopping the client,
including on the sign-in screen or while editing. A second Ctrl+C must arrive
within three seconds; otherwise confirmation expires. Background status updates
do not hide the confirmation message.

### Chat

Expand a year, then a month to browse conversations, or search history with `/`.
Years, months and conversations are ordered by last activity, newest first.
Conversations without a valid date appear last under Unknown date.
Use `c` or `:new` to start an empty conversation and focus the composer. In an open conversation, rename/archive it, choose a provider/model,
and attach a local PNG/JPEG/GIF/WebP file. Replies stream incrementally and show
tool activity. The New button starts an empty conversation. Ctrl+G sends the
message; Enter inside the composer inserts a newline.

### Training

Expand a training theme to see its index and fiches, including nested folders.
Use `:generate` to generate a fiche from a concept/theme/parent with optional
web search, or `:plan` to start a guided learning-plan chat. Generation uses SSE, including
heartbeat support, and opens the result in Training. Internal note links also
open in the Training reading pane. External links open the system browser.

### News and Anki

News hides read articles by default and groups articles by category and feed. `/` searches across the loaded
articles. Unread articles are bright cyan and bold; read articles are muted gray.
Matched search characters remain highlighted without losing the reading-state
color of the rest of the title. Read state is not appended to article titles.
Feed names are bold magenta, distinct from the cyan/gray article titles. Favicons
provided by the API appear before feed names in the hierarchy and the feed column,
as compact two-cell graphical thumbnails. Logos are cached and work without a
native terminal image protocol; missing or unsupported images leave names readable.

- **`u` / `:unread`** hides or shows read articles. The header shows the next
  action and the current mode is shown in the footer.
- **`m`** toggles read/unread for the selected article in the list or the article
  currently open. Selecting a category or feed does not modify any article.
  This action does not open or download the selected article. Marking an article
  read removes it immediately from an unread-only list; a failed update preserves
  its previous state.

Use **Space** to select/deselect articles for a batch action. Selected rows have
a purple background, and the table title/footer show the selection count.
Space on a category or feed selects/deselects all its loaded descendants, even
when collapsed. Selection follows article IDs across refreshes and remains active
when a search or unread filter hides a selected article. Spaces in text inputs
and the command/search prompt remain ordinary text.

On the list, **`m` / `:read`**, **`s` / `:star`**, and **`a` / `:actions`** apply
to the entire selection, or to the cursor's article when nothing is selected.
A mixed selection is marked read if any selected article is unread, or starred
if any selected article is unstarred; otherwise the action marks all unread or
unstars all. The actions dialog also offers explicit read/unread and star/unstar
actions, captures, labels and a shared custom instruction. Successfully processed
articles are deselected; failed articles stay selected for retry, with a success/
failure count in the status line. **Escape** or **`:clear`** clears the selection.
When an article is open, actions affect that article only.

The article view has no filter or action toolbars. Before its title, an empty
star or yellow filled star indicates its favorite state, and an explicit cyan
`UNREAD` or gray `READ` marker indicates reading state. Opening an article
automatically marks it read. Dates omit the time. Original HTML is converted to
Markdown and styled for the terminal, preserving headings, paragraphs, emphasis,
lists, links and image references; the summary is a fallback when HTML is absent.
Use **`m` / `:read`** to toggle read state, **`s` / `:star`** to toggle its star,
**`o` / `:original`** to open the original, and **`a` / `:actions`** for captures,
labels and custom instructions. Letter shortcuts are inactive while typing in an
input, editor or command prompt. News loads the server's default 30-day window.
Use **`:fetch`** to fetch RSS and **`:feeds`** to manage feeds. Manage feeds supports
subscriptions, title/category changes, unsubscribing and category rename/delete.
For category renames, enter the new name in the Category field.

Fetch RSS starts the server's background ingestion; use Refresh after it finishes.

### Anki

Anki opens on a deck list showing review, new, learning and total card counts.
On an older backend, decks still load and unavailable counts appear as `—`.
Update and restart the backend to enable deck statistics and reviews.
Deck names are yellow when reviews or learning steps are due, aqua when only new
cards are available, and gray when up to date. The STATE column also labels this
status. Nonzero review counts are green, new counts aqua, and learning counts orange.
Press Enter on a deck to expand/collapse its due and new cards
directly below it. Multiple decks can remain expanded, and their counters stay
visible. Enter on a card opens a review popup over the same list. Select Show
answer, then Again, Hard, Good or Easy to save the rating. Success closes the
popup and refreshes the expanded decks; failures keep the popup open for retry.
Close or Escape dismisses a review without rating and restores list selection.
Tab / Shift+Tab moves between popup controls. Escape on the list collapses the
selected deck (or clears an active search first).
Each rating has Anki's estimated next review interval directly below it. `—`
means the server/plugin did not provide an unambiguous four-button estimate.

Press **`u` / `:cards`** to toggle between due/new cards and **all cards**, including
future, suspended and buried cards. The header shows the next action; the table
title and footer show `due` or `all cards`. Expanded decks and local search are
preserved, and their cards reload in the selected mode. Cards load in pages of
1,000 until the deck is loaded. All-card mode is read-only: Enter opens a browse
popup and Show answer reveals the back, without rating controls or changes to the
review schedule. Switch back to due mode to review cards normally.

Press **`s` / `:sync`** in Anki to synchronize the host desktop's collection with
AnkiWeb, then reload deck counts and cards while preserving expanded decks.
The status line shows progress/errors. The desktop profile must already be
connected to AnkiWeb; synchronize other devices first to upload their changes.
Use `r` for an ordinary refresh without synchronization.

Press `c` or use `:new` to open the note creation popup: deck, front, back,
normal/reversed mode and tags. Create note saves it and closes the popup; Cancel
or Escape closes it. Anki desktop must be running with AnkiConnect enabled.
Rendered card sides respect reversed/cloze templates. Local card images load
through the authenticated server's Anki media proxy. Scheduler counters count
cards, not notes; their daily limits can differ from the searchable due/new list.

The login popup contains only username/password prompts. Enter after the
password signs in; Enter after the username moves to the password.

## Images — WezTerm and tmux

News covers/body images, note attachments and chat images are shown in a media
pane next to the document. Previous / Next navigates the document's images.
Images load asynchronously; missing images do not interrupt document reading.
PNG, JPEG, GIF (first frame) and WebP are supported.

Available `--images` modes:

- **auto** (default): selects `iterm2` when `WEZTERM_PANE` or a recognized
  `TERM_PROGRAM` identifies WezTerm/iTerm2; otherwise uses `halfcell`. This is
  environment detection, not a terminal capability probe. Use an explicit mode
  when those variables are missing, for example over SSH.
- **iterm2**: native inline images through OSC 1337, supported by WezTerm.
  Recommended for WezTerm, including under tmux.
- **sixel**: native Sixel encoding in Go. Uses reported terminal cell pixel sizes,
  falling back to 8×16 pixels when the terminal/tmux does not provide them.
- **halfcell**: portable full-color terminal glyph previews managed by tcell.
- **off**: no image requests or image pane.

For tmux 3.3+, add to `~/.tmux.conf`:

```tmux
set -g allow-passthrough on
```

Reload the configuration, then start a new session. Run:

```bash
./tui/second-brain-tui --images iterm2 https://brain.example.com
```

Native image payloads are wrapped in tmux DCS passthrough envelopes. Cursor
positioning stays pane-relative. The application flushes tcell's text layer
before drawing images and clears the previous image on document changes,
resizes and modal dialogs. If your terminal does not render native images,
use `--images halfcell`.

The terminal interface uses English labels and a dark palette. Markdown is
formatted for the terminal; browser-only Mermaid diagrams and typeset LaTeX
remain textual. SVG and animated playback are not implemented.

## Verification

### Diagnostic output

Run `./tui/second-brain-tui --debug https://brain.example.com`, open Anki, then
quit with `:quit` or Ctrl+C twice. A bounded debug trace prints to stderr after
the terminal UI closes. It includes HTTP paths/statuses and received Anki deck
statistics, including which response fields are actually present. Passwords,
cookies, query parameters and card contents are omitted. Deck names are included.
To capture the trace, append `2> /tmp/second-brain-debug.log` to the command.

```bash
go -C tui test ./...
go -C tui vet ./...
# Optional race detector; requires CGO and a C compiler:
CGO_ENABLED=1 go -C tui test -race ./...
```

Tests cover HTTP sessions, reverse-proxy prefixes, SSE framing and cancellation,
image authentication/decoding/protocol encoding, Markdown/Obsidian assets, and
the main workflows on a tcell simulation screen, including command entry,
hierarchical expansion, fuzzy ranking, prompt completion/cancellation, explicit
leaf activation, timed quit confirmation, and news read-state shortcuts/colors.
Native display still needs to
be checked in a real WezTerm/tmux session.
