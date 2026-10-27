# tdg-news

A small CLI for **Tribune de Genève (tdg.ch)** with two modes:

- **public** — Tamedia's partner RSS feeds: headlines plus the one-paragraph lead.
- **subscriber** — your own tdg.ch session, which unlocks the site's listing pages
  (structured teaser JSON for every article on the front page and each section) and
  the **complete article body** instead of the paywall teaser.

Built for the Hearth profile's "Geneva morning brief" cron job, which now summarises
from full article text rather than RSS leads.

## Requirements

- Python 3.8+ (`argparse`, `json`, `urllib`, `sqlite3`, `html.parser` — all stdlib).
- `cryptography` **only** for `token --sync` (reading the browser cookie DB).
- Network access to `www.tdg.ch` and `partner-feeds.publishing.tamedia.ch`.
- A valid tdg.ch subscription session for subscriber mode.

## Setup (subscription mode)

```bash
python3 tdg.py token --sync          # import the refreshToken cookie from local Chromium/Brave/Chrome
python3 tdg.py token                 # verify: hasSubscription should be True
```

`token --sync` copies the newest `tdg.ch` `refreshToken` cookie from a Chrome-family
profile, decrypts it with the "Chrome Safe Storage" key from the OS keyring
(`secret-tool`), and writes it to `~/.config/tdg/refresh_token` (mode 600).

The stored token *is* a session credential — treat it like a password. It lives
outside the repo and is never committed. Override the location with
`TDG_TOKEN_FILE=/path` or the token itself with `TDG_REFRESH_TOKEN`.

Token lifetime is long (the Piano refresh token currently expires in ~1 year), but if
the brief ever comes back with `paywalled: true`, the cookie was rotated or revoked:
open tdg.ch in Chromium once, then re-run `token --sync`.

## Usage

```bash
# subscriber: today's published surface (front page + sections + news sitemap)
python3 tdg.py index --hours 18 --json
python3 tdg.py index --hours 24 --category geneve --json
python3 tdg.py index --hours 18 --only-topics immobilier,taux,ai,tech --json
python3 tdg.py index --hours 48 --sections ,geneve,economie --pages 3 --json
python3 tdg.py index --sections ,geneve,suisse --json      # "" = front page

# subscriber: complete article text (several at once, one process)
python3 tdg.py article 764245742790 101232672867 --json
python3 tdg.py article https://www.tdg.ch/some-slug-123456789012

# public fallback (no token needed)
python3 tdg.py top 15 --json
python3 tdg.py search tren
python3 tdg.py top 10 --hours 24 --md
```

### `index`

Scrapes `teaser-data` JSON blocks from the tdg.ch front page and the section list
below (Geneva sections, Suisse, votations, opinion, société, économie, high-tech,
savoirs/technologie, monde), merges `https://www.tdg.ch/news.xml`, dedupes by article
id and sorts newest first. `--pages N` walks `/<section>/page/2..N` for deeper archive
coverage (the listing is path-paginated; `?page=` does nothing).

Fields per item: `id`, `title`, `kicker`, `lead`, `url`, `section` (categoryPath),
`is_premium`, `is_opinion`, `type`, `authors`, `published`, `source`, and `topics` when
the item matches a standing interest. `subscriber_mode` in the JSON envelope tells you
whether the token was used; without a token it degrades to RSS + sitemap titles (no
leads, no premium flag).

### Standing interests (`topics`)

`TOPIC_PATTERNS` in the script tags items with Robert's standing interests, and any brief
built from this tool must keep them:

| tag | matches |
|---|---|
| `immobilier` | property, rents, logement, PPE, zoning, terrains, Asloca, mortgages |
| `taux` | SNB/BNS, SARON, policy and mortgage rates, inflation, ECB/Fed, savings |
| `ai` | IA, ChatGPT/Claude/Gemini, LLMs, chips, data centres, autonomous agents |
| `tech` | cyber, platforms, start-ups, digitalisation, Big Tech, software |

`--only-topics immobilier,taux,ai` returns just that subset — the reliable way for an
agent to assemble the must-include list instead of eyeballing 100 headlines. Extend the
patterns in `TOPIC_PATTERNS` when a new interest appears; keep them narrow enough that
"taux de chômage" and "taux de participation" do not trigger `taux`.

### `article`

Returns `id`, `url`, `title`, `lead`, `section`, `authors`, `published`, `modified`,
`is_premium`, `paywalled`, `warning`, `words`, `body` (full text, paragraphs joined) and
`paragraphs`. `paywalled: true` plus a `warning` means the token was missing or stale —
the body then only holds the free teaser. Body extraction targets
`article[data-article-body]`, keeping `p.articleParagraph`, `h2.crosshead` and
blockquotes, and skipping related-teaser asides, figures and scripts.

### Authentication flow (why the code looks odd)

tdg.ch answers a paywalled article request with `302` + `Set-Cookie: entitlementToken`
and redirects back to the same URL, so a plain fetch redirect-loops. The tool bootstraps
instead: one `HEAD /` with the `refreshToken` cookie mints a short-lived (~30 min)
subscriber `entitlementToken`, which is then sent directly on article requests. Redirects
are followed manually with a cookie jar as a fallback.

## Integration

Powers the **Hearth** profile cron job **"Geneva morning brief (tdg.ch)"**
(job id `d33a2cfd15f7`, `30 7 * * *`, delivered to Slack `#geneva-news`).

The job prompt runs `index --hours 18 --json`, then `index --hours 18 --only-topics
immobilier,taux,ai,tech --json` for the standing-interest set, picks 6–8 notable Geneva
stories, fetches them plus every flagged item in a single `article <ids> --json` call, and
writes English summaries from the full bodies with clickable links. Property, rate and AI
stories are **never dropped** for length or because they are not Geneva-specific — they
land in a dedicated "Vos thèmes" section. The prompt includes a self-healing step
(`token --sync`, then one retry) and a `⚠️ Abonnement TDG non reconnu` fallback line if
the subscription is not recognised.

### Re-homing on a fresh host

1. Clone this repo into a stable location (e.g. `~/tdg-news`).
2. Run `python3 tdg.py token --sync`, then `python3 tdg.py token` to confirm
   `hasSubscription: True`.
3. Point the cron job at the new path:
   ```bash
   hermes -p hearth cron edit d33a2cfd15f7 --prompt "<prompt with the path replaced>"
   ```
4. Test fire **in the background** (a run takes 1–5 minutes; killing the CLI mid-run
   marks the execution `unknown` and suppresses delivery):
   ```bash
   hermes -p hearth cron run d33a2cfd15f7
   ```
   Then check `~/.hermes/profiles/hearth/cron/output/d33a2cfd15f7/`.

## Caveats

- `index` covers the current page of each section; `--pages N` walks deeper
  (`/<section>/page/2..N`). Combined with `news.xml` this is close to "everything published
  today", not the full archive.
- Topic tagging is keyword-based: it catches the obvious phrasing and can miss a story
  that discusses rents or rates without using the expected words. Treat `topics` as a
  floor for must-include items, not a ceiling.
- Live tickers (`type: tickers`) have a body that changes with the event; summaries of
  them age quickly.
- Respect the paywall: this reads pages exactly like a logged-in browser would.

## License

MIT
