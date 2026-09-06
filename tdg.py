#!/usr/bin/env python3
"""tdg-news — Tribune de Genève Geneva top-articles CLI (stdlib only)."""
import argparse, json, sys, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

BASE = "https://partner-feeds.publishing.tamedia.ch/rss/tdg"
GENEVA = ["geneve", "geneve/politique-genevoise", "geneve/faits-divers-geneve",
          "geneve/grand-geneve", "geneve/geneve-internationale", "geneve/herrmann",
          "geneve/encre-bleue", "geneve/signe-geneve"]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

def fetch(feed, timeout=20):
    url = f"{BASE}/{feed}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            root = ET.fromstring(r.read())
    except Exception:
        return []
    ch = root.find("channel")
    items = []
    for it in (ch.findall("item") if ch is not None else []):
        def txt(tag):
            el = it.find(tag)
            return el.text.strip() if el is not None and el.text else ""
        link = txt("link") or txt("guid")
        if link and not link.startswith("https://www.tdg.ch/"):
            continue
        px = txt("pubDate")
        dt = None
        if px:
            for f in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
                try:
                    dt = datetime.strptime(px, f).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    pass
        items.append({
            "title": txt("title"), "url": link, "lead": txt("description"),
            "author": txt("authors") or txt("author"),
            "published": dt.isoformat() if dt else None,
        })
    return items

def collect(feeds):
    seen = {}
    for f in feeds:
        for it in fetch(f):
            k = it["url"] or it["title"]
            if k and k not in seen:
                seen[k] = it
    return sorted(seen.values(), key=lambda i: i["published"] or "", reverse=True)
def main():
    ap = argparse.ArgumentParser(prog="tdg-news", description="Tribune de Genève Geneva top articles")
    ap.add_argument("command", nargs="?", default="top", help="top | search")
    ap.add_argument("arg", nargs="?", default="", help="count or search keyword")
    ap.add_argument("--hours", type=int, default=0, help="only articles from last N hours")
    ap.add_argument("--feeds", default=None, help="comma-separated feed override")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", action="store_true", help="markdown with clickable links")
    a = ap.parse_args()
    feeds = [x.strip() for x in a.feeds.split(",") if x.strip()] if a.feeds else GENEVA
    items = collect(feeds)
    if a.hours:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=a.hours)).isoformat()
        items = [i for i in items if i["published"] and i["published"] >= cutoff]
    if a.command == "search":
        kw = a.arg.lower()
        items = [i for i in items if kw in (i["title"] + " " + i["lead"]).lower()]
    n = int(a.arg) if (a.arg and a.command != "search") else (len(items) if a.command == "search" else 10)
    items = items[:n] if n > 0 else items
    if a.json:
        print(json.dumps(items, ensure_ascii=False))
        return
    if a.md:
        for i in items:
            lead = (" — " + i["lead"]) if i["lead"] else ""
            print(f"- [{i['title']}]({i['url']}){lead}")
        return
    for i in items:
        when = i["published"][:16].replace("T", " ") if i["published"] else "n/a"
        print(f"[{when}] {i['title']} ({i['author']})")
        if i["lead"]:
            print(f"    {i['lead']}")
        print(f"    {i['url']}")

if __name__ == "__main__":
    sys.exit(main())
