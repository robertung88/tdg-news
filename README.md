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

The Hearth profile's **Geneva morning brief (tdg.ch)** cron job runs this script and asks the agent to compile the 6–8 most notable stories into a short Telegram brief:

```bash
python3 /home/roblinux/tdg-news/tdg.py top 15 --json
```

The path is host-specific; point the path at wherever you clone this repo.

## License

MIT
