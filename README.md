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
below (Geneva sections, Suisse, votations, opinion, société), merges
`https://www.tdg.ch/news.xml`, dedupes by article id and sorts newest first.

Fields per item: `id`, `title`, `kicker`, `lead`, `url`, `section` (categoryPath),
`is_premium`, `is_opinion`, `type`, `authors`, `published`, `source`.
`subscriber_mode` in the JSON envelope tells you whether the token was used; without a
token it degrades to RSS + sitemap titles (no leads, no premium flag).

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

The job prompt runs `index --hours 18 --json`, picks 6–8 notable Geneva stories, fetches
them in a single `article <ids> --json` call, and writes English summaries from the full
bodies with clickable links. It includes a self-healing step (`token --sync`, then one
retry) and a `⚠️ Abonnement TDG non reconnu` fallback line if the subscription is not
recognised.

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

- Listing pages paginate client-side: `?page=N` returns the same teasers, so `index`
  covers each section's current teaser set only. Combined with `news.xml` this is
  close to "everything published today", not a full archive.
- Live tickers (`type: tickers`) have a body that changes with the event; summaries of
  them age quickly.
- Respect the paywall: this reads pages exactly like a logged-in browser would.

## License

MIT
