---
updated: 2026-04-25
---

# Vault index

This file is the brain's map of the vault. Every chat session prepends
its content to the LLM's system prompt — keep it accurate. Update it
when you reorganize.

## Top-level folders

- `Books/` — Notes about books I'm reading or have read.
- `Bug bounty/` — Notes about hacking and bug-bounty work.
- `Divers/` — Miscellaneous notes that don't fit elsewhere.
- `Excalidraw/` — Drawings made via the Excalidraw plugin (handle as binary attachments).
- `Exkalibur/` — Notes about the "Excalibur" treasure-hunt project.
- `Famille/` — Family notes (school meetings, family events, life logistics).
- `Feeds/` — Captures from my FreshRSS module.
  - `Articles/` — One note per article I asked the module to file in full.
  - `Notes/` — One note per information I asked to keep. **The nightly Organize
    job MUST merge these into a single per-theme markdown file under `Notes/`**
    — they are intermediate captures, not the final form.
  - `Youtube/` — One note per YouTube video I want to watch (FreshRSS also
    listens to YouTube channel feeds). **The nightly Organize job MUST append
    each entry into a dedicated "Watch list" markdown file and remove the
    source notes after moving them**.
- `files/` — Binary attachments (PDFs, images) referenced by notes.
- `Ideas/` — Raw ideas. Promote into `Notes/` once developed.
- `Inbox/` — Unclassified captures. The nightly Organize job promotes
  these into the right topic folder.
- `Journal/` — Daily journal. Today's note sits at `Journal/YYYY-MM-DD.md`;
  the nightly job archives prior days into `Journal/YYYY/MM/YYYY-MM-DD.md`.
- `Logger/`
  - `Opencode/` — Archived conversations from my coding agent.
- `Maker/` — Maker / 3D printing / modeling / electronics tutorials and projects.
- `Notes/` — The main classified knowledge base. **Most "remember this"
  captures end up here, organized by topic.**
- `People management/` — People management, mindset, leadership notes.
- `ReadItLater Inbox/` — Captured by the ReadItLater Obsidian plugin.
  Treat as another inbox to drain (same handling as `Inbox/`).
- `Science/` — Science notes.
- `Tech/` — Tech and computing notes.
- `Templates/` — Templates (daily / monthly notes, etc). **Read-only for
  the brain — these are the user's own scaffolds; never refactor them
  unless explicitly asked**.

## Loose files at the vault root

- `Cheatsheet.md` — General tech command cheatsheet.
- `S3NS cheatsheet.md` — Tech command cheatsheet, S3NS-specific (my previous employer).
- `INDEX.md` — This file.
- `USER.md` — Facts about me (read by the brain at every session).
- `PREFERENCES.md` — How I want the brain to operate (read by the brain at every session).

## Conventions

- Wikilinks `[[Like This]]` everywhere — no plain mentions when there's a matching note.
- Frontmatter `tags:` are flat, lowercase-kebab.
- Default landing for unclassified captures: `Inbox/`.
- The brain may MOVE notes during the nightly Organize, but should not rename
  them silently — keep the original filename when possible.

## Nightly Organize — required behaviors

1. Archive yesterday's `Journal/YYYY-MM-DD.md` into the dated subfolder
   structure.
2. Drain `Inbox/` and `ReadItLater Inbox/`: classify each note and move it
   into the right topic folder under `Notes/`, `Tech/`, `Science/`, etc.
3. Drain `Feeds/Notes/`: merge each captured information into the most
   relevant per-theme markdown file under `Notes/` (or the matching topic
   folder). Remove the source note after merging.
4. Drain `Feeds/Youtube/`: append each entry to a single
   `Notes/Watch list.md` (or equivalent) and remove the source note.
5. Suggest wikilinks across topic notes.
6. Fix grammar, spelling, and structure — preserve the user's voice.
