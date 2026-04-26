# News synthesis instructions

Drop a copy of this file at the root of your Obsidian vault as
`NEWS_SYNTHESIS.md` — the tagger reads it on every pass and appends
its content to the built-in default prompt. Edit freely; the next
cluster run picks up the changes (no restart needed).

These rules **extend** the defaults — they don't replace them. The
defaults already cover JSON-schema mechanics, slug shape, tag
budget, and basic forbidden categories. Use this file for your own
feed-specific tuning.

## Tag granularity — stay at the family level

Group every variant of the same subject under one canonical tag.
Don't split a single concept across version / edition / suffix tags.

  BAD:  ["usb", "usb-1", "usb-2", "usb-3", "usb-c"]
  GOOD: ["usb"]

  BAD:  ["python", "python-3", "python-3.12", "python-3.13"]
  GOOD: ["python"]

  BAD:  ["chatgpt", "chatgpt-plus", "chatgpt-pro", "chatgpt-team"]
  GOOD: ["chatgpt"]

A version qualifier (e.g. `gpt-5.5`, `ubuntu-26.04`) is only justified
when the version itself is the headline of the article — e.g. an
article specifically announcing that release.

## Cap the count

5 to 10 tags per article is the sweet spot. The schema permits up to
20, but going wide produces a long tail of one-article topics that
never cluster. Concentrate on the genuinely-discussed entities.

## Forbidden categories — never tag these

These never form useful trends; drop them entirely:

- News providers, aggregators, and publications:
  hacker-news, reddit, techcrunch, the-verge, le-monde, franceinfo,
  ars-technica, wired, lemde, tbpn, deus-ex-silicium, ...
- Generic medium descriptors:
  podcast, newsletter, video, youtube, blog
- Generic categories:
  news, tech, world, today, ai, science, politics, business
- Section headers from feeds:
  front-page, homepage, weekly, daily-digest

## Topic preferences (edit to match your interests)

Favour specific entities the article substantively discusses:

- Companies and orgs: openai, anthropic, mozilla, eu, apple
- People: sam-altman, elon-musk, macron
- Products at the family level: gpt, claude, firefox, ios, ubuntu
- Places when central to the story: france, ukraine, china
- Recurring events: apple-q1-earnings, ces-2026, kubecon
- Specific policies: pension-reform, ai-act, digital-markets-act

## (Optional) language preference

If you want consistent slugs across French and English feeds, pick
one canonical language per concept. For example, prefer
`pension-reform` over `reforme-des-retraites` so a French article
and an English article on the same topic share the tag.
