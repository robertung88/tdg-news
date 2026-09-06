# tdg-news

A tiny, dependency-free CLI for pulling the latest top articles from **Tribune de Genève (tdg.ch)** — Geneva's daily newspaper — via Tamedia's public partner RSS feeds.

Built as a reusable tool for the Hearth agent's "Geneva morning brief" cron job, so the script can be versioned, referenced across setups, and re-homed on a fresh host without digging through an abandoned Docker sandbox.

## Requirements

- Python 3.8+ (stdlib only — `argparse`, `json`, `urllib`, `xml.etree.ElementTree`).
- No `pip install`. No API key. Network access to `partner-feeds.publishing.tamedia.ch`.

## Install / Run

Just clone and run directly — the script is self-contained and executable:

```bash
git clone https://github.com/robertung88/tdg-news.git
cd tdg-news

python3 tdg.py top 15 --json      # top 15 Geneva articles as JSON
python3 tdg.py top 10             # human-readable list
python3 tdg.py top 8 --md         # Markdown with clickable links
python3 tdg.py search tren           # search all articles for "tren"
python3 tdg.py top 10 --hours 24     # only articles from the last 24h
```

## Usage

```
usage: tdg-news [-h] [command] [arg] [--hours HOURS] [--feeds FEEDS] [--json] [--md]

Command:
  top       (default) latest top articles
  search    filter articles whose title+lead contain the search keyword

Arguments:
  arg       for `top`: number of articles to return (default 10)
            for `search`: the keyword to match

Options:
  --hours N     only return articles published within the last N hours
  --feeds LIST  comma-separated feed slug override (default: Geneva feeds)
  --json        output as JSON
  --md          output as Markdown with clickable `[Titre](url)` links
```

## Feeds

By default the CLI aggregates these Geneva sections from `https://partner-feeds.publishing.tamedia.ch/rss/tdg`:

- `geneve`
- `geneve/politique-genevoise`
- `geneve/faits-divers-geneve`
- `geneve/grand-geneve`
- `geneve/geneve-internationale`
- `geneve/herrmann`
- `geneve/encre-bleue`
- `geneve/signe-geneve`

Override with `--feeds slug,slug`. Only items whose URL resolves to `https://www.tdg.ch/` are kept.

## Output

Each item includes `title`, `url`, `lead` (the article's lead/description text), `author`, and `published` (ISO-8601 UTC timestamp) — or `null` if the feed doesn't provide it. With `--json` the full list is printed as a JSON array.

## Integration

This script exists to power the **Hearth** profile's cron job **"Geneva morning brief (tdg.ch)"** (`job id d33a2cfd15f7`), which builds the daily Geneva news brief delivered to the "House of Ung" Telegram chat.

The job is an LLM-driven agent task — it does not call the script directly. Instead, its prompt instructs the agent to run the script in the terminal and curate the result:

> Build a concise Geneva-morning news brief from the Tribune de Genève. Run in the terminal: `python3 /home/roblinux/tdg-news/tdg.py top 15 --json`. Pick the 6–8 most notable Geneva stories. Present EVERY headline as a CLICKABLE link in Telegram markdown — [Titre](url) — each followed by a 1–2 sentence English summary from that item's lead text. Start with a one-line 'À la une' opener. Group by theme (politique, faits divers, grand Genève, société...) if it reads cleanly. Keep the whole thing under ~250 words. Every headline MUST be a clickable link to its article. If the feed fetch fails or returns nothing, say so plainly and do NOT invent stories.

Job summary:

| Field | Value |
|-------|-------|
| Profile | `hearth` |
| Job ID | `d33a2cfd15f7` |
| Schedule | `30 7 * * *` (07:30 CET, daily) |
| Deliver | `origin` → Telegram "House of Ung" (`-1003950035504`) |
| Model | `deepseek-v4-flash-vision-exp` (served from sparky) |

### Re-homing on a fresh host

The script path is **host-specific**. On the Omarchy host it lives at `/home/roblinux/tdg-news/tdg.py`; on the old Mac Mini Docker setup it was `/root/tdg-news/tdg.py`. When cloning to a new host:

1. Clone this repo into a stable location (e.g. `~/tdg-news`).
2. Update the cron job prompt to point at the local path:
   ```bash
   hermes -p hearth cron edit d33a2cfd15f7 --prompt "<the above prompt, with the path replaced>"
   ```
3. Fire a test run to confirm it delivers:
   ```bash
   hermes -p hearth cron run d33a2cfd15f7
   ```

The script only needs stock Python 3.8+ and outbound access to `partner-feeds.publishing.tamedia.ch`.

## License

MIT
