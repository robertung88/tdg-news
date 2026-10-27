#!/usr/bin/env python3
"""tdg-news — Tribune de Genève CLI (stdlib only, subscription-aware).

Two layers:

  * public layer  — Tamedia partner RSS: headlines + short leads only.
  * subscriber layer — your own tdg.ch session (a Piano "refreshToken" cookie)
    unlocks (a) the site's listing pages, which expose structured teaser JSON
    for every article on the front page and each section, and (b) the complete
    article body instead of the 1-paragraph paywall teaser.

Commands
  top [N]           public RSS headlines (no subscription needed)
  search KEYWORD    filter public RSS items
  index [N]         subscriber listing scrape: front page + sections + news sitemap
  article ID|URL…   full article text (needs the subscription token)
  token             show token status; --sync imports it from your browser,
                    --set VALUE stores one directly

Token file: ~/.config/tdg/refresh_token (mode 600). See README.
"""
import argparse
import base64
import hashlib
import html as htmlmod
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

BASE = "https://partner-feeds.publishing.tamedia.ch/rss/tdg"
SITE = "https://www.tdg.ch"
NEWS_SITEMAP = "https://www.tdg.ch/news.xml"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")

TOKEN_PATH = os.path.expanduser(os.environ.get("TDG_TOKEN_FILE", "~/.config/tdg/refresh_token"))

GENEVA_FEEDS = ["geneve", "geneve/politique-genevoise", "geneve/faits-divers-geneve",
                "geneve/grand-geneve", "geneve/geneve-internationale", "geneve/herrmann",
                "geneve/encre-bleue", "geneve/signe-geneve"]

# Listing pages to scrape in subscriber mode. "" is the front page.
INDEX_SECTIONS = ["", "geneve", "geneve/politique-genevoise", "geneve/faits-divers-geneve",
                  "geneve/grand-geneve", "geneve/geneve-internationale", "geneve/herrmann",
                  "geneve/encre-bleue", "geneve/signe-geneve", "suisse", "suisse/votations",
                  "suisse/suisse-romande", "opinion", "societe", "economie", "high-tech",
                  "savoirs/technologie", "savoirs", "monde"]

# Standing interests: items matching these must never be dropped from a brief,
# whatever else is selected. French + English keywords, case-insensitive.
TOPIC_PATTERNS = {
    "immobilier": [
        r"\bimmobili", r"\bloyers?\b", r"\blogements?\b", r"\blocataires?\b", r"\bPPE\b",
        r"\bpropriétaires?\b", r"\bbail\b", r"\bbaux\b", r"permis de construire",
        r"\bparcelle", r"\bzone villa", r"promotion immobili", r"\bAsloca\b",
        r"\bhypothèqu", r"surface commerciale", r"\bmètres carrés", r"\bvacance",
        r"\brégie immobili", r"marché immobilier", r"\bquote-part", r"\bterrain",
    ],
    "taux": [
        r"\bSNB\b", r"\bBNS\b", r"banque nationale", r"\bSARON\b", r"politique monétaire",
        r"taux (?:directeur|hypothécaire|d['’]intérêt|à court terme|négatifs?)",
        r"\bhypothéq", r"\binflation\b", r"taux d['’]intérêt", r"\bBCE\b", r"\bFed\b",
        r"taux de base", r"obligations? souveraines", r"\bépargne\b", r"banques? centrales?",
        r"coût du crédit", r"rendement obligataire",
    ],
    "ai": [
        r"\bIA\b", r"intelligence artificielle", r"\bLLMs?\b", r"ChatGPT", r"OpenAI",
        r"Anthropic", r"\bClaude\b", r"\bGemini\b", r"Nvidia", r"\balgorithm", r"\brobots?\b",
        r"data ?cent(?:er|re)", r"semi-conducteurs?", r"générative?", r"DeepSeek",
        r"Mistral", r"apprentissage automatique", r"machine learning", r"\bagents? autonomes?",
    ],
    "tech": [
        r"\bcyber", r"\bnumérique\b", r"\bdigital", r"\bstart-?ups?\b", r"automatisation",
        r"\bGAFAM\b", r"\bGoogle\b", r"\bMicrosoft\b", r"\bApple\b", r"\bMeta\b",
        r"\bAmazon\b", r"\btechnolog", r"\bplateformes?\b", r"\blogiciel",
    ],
}
_TOPIC_RE = {k: re.compile("|".join(v), re.I) for k, v in TOPIC_PATTERNS.items()}


def tag_topics(items, text_fields=("title", "lead", "kicker")):
    for it in items:
        blob = " \n ".join(str(it.get(f) or "") for f in text_fields)
        hits = [k for k, rx in _TOPIC_RE.items() if rx.search(blob)]
        if hits:
            it["topics"] = hits
    return items


# ---------------------------------------------------------------- HTTP plumbing

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _parse_setcookie(headers):
    out = {}
    for k, v in headers:
        if k.lower() == "set-cookie":
            first = v.split(";", 1)[0]
            if "=" in first:
                name, val = first.split("=", 1)
                out[name.strip()] = val.strip()
    return out


def http_get(url, cookies=None, headers=None, timeout=30, max_redirects=6, method="GET"):
    """GET/HEAD following redirects manually while carrying cookies.

    tdg.ch answers article requests with 302 + Set-Cookie (entitlement token)
    and then redirects back to the same URL, so a naive fetch loops.
    Returns (status, body_text, cookies_out).
    """
    jar = dict(cookies or {})
    hdrs = {"User-Agent": UA, "Accept-Language": "fr-CH,fr;q=0.9,en;q=0.8"}
    if headers:
        hdrs.update(headers)
    for _ in range(max_redirects + 1):
        if jar:
            hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
        req = urllib.request.Request(url, headers=hdrs, method=method)
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                body = r.read() if method != "HEAD" else b""
                jar.update(_parse_setcookie(r.headers.items()))
                return r.status, body.decode("utf-8", "replace"), jar
        except urllib.error.HTTPError as e:
            jar.update(_parse_setcookie(e.headers.items()))
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("location")
                if not loc:
                    return e.code, "", jar
                url = urllib.parse.urljoin(url, loc)
                continue
            body = e.read() if method != "HEAD" else b""
            return e.code, body.decode("utf-8", "replace"), jar
        except Exception:
            return 0, "", jar
    return 0, "", jar


# ------------------------------------------------------------------- token mgmt

def load_token():
    tok = os.environ.get("TDG_REFRESH_TOKEN", "").strip()
    if tok:
        return tok
    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_token(tok):
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(tok.strip() + "\n")
    os.chmod(TOKEN_PATH, 0o600)
    return TOKEN_PATH


def _jwt_body(tok):
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


_BROWSER_CANDIDATES = [
    ("chromium", os.path.expanduser("~/.config/chromium")),
    ("chrome", os.path.expanduser("~/.config/google-chrome")),
    ("Brave Browser", os.path.expanduser("~/.config/BraveSoftware/Brave-Browser")),
    ("chrome", os.path.expanduser("~/.config/chrome-beta")),
]


def _keyring_password(app):
    for attrs in (["application", app], ["application", "chrome"]):
        try:
            r = subprocess.run(["secret-tool", "lookup"] + attrs,
                               capture_output=True, text=True, timeout=10)
            pw = r.stdout.strip()
            if pw:
                return pw.encode()
        except Exception:
            continue
    return b"peanuts"


def _decrypt_cookie(enc, password):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1, 16)
    dec = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    pt = dec.update(enc[3:]) + dec.finalize()
    if not pt:
        return ""
    pt = pt[:-pt[-1]]
    for cand in (pt, pt[32:]):
        try:
            return cand.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return pt.decode("utf-8", "replace")


def browser_token():
    """Newest tdg.ch refreshToken sitting in a local Chrome-family cookie DB."""
    best = None
    for app, root in _BROWSER_CANDIDATES:
        if not os.path.isdir(root):
            continue
        for prof in ("Default", "Profile 1", "Profile 2", "Profile 3"):
            db = os.path.join(root, prof, "Cookies")
            if not os.path.isfile(db):
                continue
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
            tmp.close()
            try:
                shutil.copy2(db, tmp.name)
                con = sqlite3.connect(tmp.name)
                rows = con.execute(
                    "select name, encrypted_value, value, expires_utc from cookies "
                    "where host_key like '%tdg.ch' and name in ('refreshToken','entitlementToken')"
                ).fetchall()
                con.close()
            except Exception:
                rows = []
            finally:
                os.unlink(tmp.name)
            pw = _keyring_password(app)
            for name, enc, plain, exp in rows:
                if name != "refreshToken":
                    continue
                val = plain or ""
                if not val and enc:
                    try:
                        if enc[:3] in (b"v10", b"v11"):
                            val = _decrypt_cookie(enc, pw)
                        else:
                            val = enc.decode("utf-8", "replace")
                    except Exception:
                        val = ""
                if val and (best is None or (exp or 0) > best[0]):
                    best = (exp or 0, val)
    return best[1] if best else ""


# -------------------------------------------------------------- subscription API

class Subscriber:
    """Bootstraps a short-lived entitlement token from the stored refresh token."""

    def __init__(self, token=None, verbose=False):
        self.token = token or load_token()
        self.verbose = verbose
        self._ent = None
        self._ent_exp = 0
        self._jar = {}

    @property
    def configured(self):
        return bool(self.token)

    def _bootstrap(self, force=False):
        if self._ent and not force and time.time() < self._ent_exp - 60:
            return self._ent
        if not self.token:
            return None
        status, _, jar = http_get(SITE + "/", cookies={"refreshToken": self.token},
                                 method="HEAD")
        ent = jar.get("entitlementToken", "")
        if not ent:
            # HEAD sometimes skipped by CDN — retry with a light GET
            status, _, jar = http_get(SITE + "/pourquoi", cookies={"refreshToken": self.token})
            ent = jar.get("entitlementToken", "")
        body = _jwt_body(ent)
        if not body:
            return None
        cv = body.get("customVars", {})
        exp = body.get("exp", int(time.time()) + 900)
        self._ent, self._ent_exp, self._jar = ent, exp, jar
        if self.verbose:
            print(f"[tdg] entitlement userType={cv.get('userType')} "
                  f"hasSubscription={cv.get('hasSubscription')}", file=sys.stderr)
        return ent

    def subscribed(self):
        body = _jwt_body(self._bootstrap() or "")
        return bool(body.get("customVars", {}).get("hasSubscription"))

    def get(self, url, retry=True):
        ent = self._bootstrap()
        cookies = self._cookies()
        status, text, _ = http_get(url, cookies=cookies)
        if retry and status in (0, 301, 302, 401, 403) or (retry and "Les articles ABO" in text and self.token):
            self._bootstrap(force=True)
            status, text, _ = http_get(url, cookies=self._cookies())
        return status, text

    def _cookies(self):
        # the reader requires the refresh token cookie alongside the entitlement
        # token; entitlement alone still renders the ABO paywall overlay
        ck = {}
        if self._ent:
            ck["entitlementToken"] = self._ent
        if self.token:
            ck["refreshToken"] = self.token
        return ck


# ---------------------------------------------------------------- RSS (public)

def fetch_feed(feed, timeout=20):
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
        for it in fetch_feed(f):
            k = it["url"] or it["title"]
            if k and k not in seen:
                seen[k] = it
    return sorted(seen.values(), key=lambda i: i["published"] or "", reverse=True)


# ------------------------------------------------------------ listing teasers

_TEASER_RE = re.compile(
    r'<script id="teaser-data-(\d+)" type="application/json">(.*?)</script>', re.S)


def _author_names(authors):
    names = []
    for a in authors or []:
        if isinstance(a, dict) and a.get("name"):
            names.append(a["name"])
        elif isinstance(a, str):
            names.append(a)
    return names


def parse_teasers(page_html):
    out = []
    for aid, blob in _TEASER_RE.findall(page_html):
        try:
            d = json.loads(htmlmod.unescape(blob)).get("teaser", {})
        except Exception:
            continue
        url = d.get("url") or ""
        if url.startswith("/"):
            url = SITE + url
        if not url:
            continue
        out.append({
            "id": str(d.get("id") or aid),
            "title": d.get("title") or "",
            "kicker": d.get("titleHeader") or "",
            "lead": d.get("lead") or "",
            "url": url,
            "section": d.get("categoryPath") or "",
            "is_premium": bool(d.get("isPremium")),
            "is_opinion": bool(d.get("isOpinion")),
            "type": d.get("type") or "",
            "authors": _author_names(d.get("authors")),
            "published": d.get("published") or d.get("date"),
            "source": "web",
        })
    return out


def parse_news_sitemap():
    try:
        req = urllib.request.Request(NEWS_SITEMAP, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            root = ET.fromstring(r.read())
    except Exception:
        return []
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9",
          "news": "http://www.google.com/schemas/sitemap-news/0.9"}
    out = []
    for u in root.findall("s:url", ns):
        loc = u.findtext("s:loc", default="", namespaces=ns)
        title = u.findtext("news:news/news:title", default="", namespaces=ns)
        pub = u.findtext("news:news/news:publication_date", default="", namespaces=ns)
        if not loc:
            continue
        aid = re.search(r"-(\d{9,12})$", loc)
        out.append({
            "id": aid.group(1) if aid else loc,
            "title": title, "kicker": "", "lead": "", "url": loc,
            "section": "", "is_premium": None, "is_opinion": None,
            "type": "", "authors": [], "published": pub, "source": "news-sitemap",
        })
    return out


def build_index(sections=None, hours=0, limit=0, include_sitemap=True, verbose=False,
                pages=1):
    sub = Subscriber(verbose=verbose)
    items = []
    if sub.configured:
        for sec in (sections if sections is not None else INDEX_SECTIONS):
            base = SITE + "/" + sec.rstrip("/") if sec else SITE
            targets = [base]
            if sec and pages > 1:
                targets += [f"{base}/page/{n}" for n in range(2, pages + 1)]
            for url in targets:
                status, page = sub.get(url)
                found = parse_teasers(page)
                if verbose:
                    print(f"[tdg] {url} -> {status} {len(found)} teasers", file=sys.stderr)
                items.extend(found)
    else:
        items = [dict(i, id=i["url"], is_premium=None, is_opinion=None, kicker="",
                      section="", type="", authors=[i["author"]] if i.get("author") else [],
                      source="rss")
                 for i in collect(GENEVA_FEEDS)]
    if include_sitemap:
        items.extend(parse_news_sitemap())

    merged = {}
    for it in items:
        key = it.get("id") or it.get("url")
        if not key:
            continue
        cur = merged.get(key)
        if cur is None:
            merged[key] = it
            continue
        # keep the richest record (web teaser beats sitemap stub)
        if len(it.get("lead") or "") > len(cur.get("lead") or ""):
            base = dict(it)
            for k, v in cur.items():
                if base.get(k) in (None, "", []) and v not in (None, "", []):
                    base[k] = v
            merged[key] = base
        else:
            for k, v in it.items():
                if cur.get(k) in (None, "", []) and v not in (None, "", []):
                    cur[k] = v

    out = list(merged.values())
    if hours:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        out = [i for i in out if i.get("published") and i["published"] >= cutoff]
    out.sort(key=lambda i: i.get("published") or "", reverse=True)
    tag_topics(out)
    if limit:
        out = out[:limit]
    return out, sub.configured


# ------------------------------------------------------------ article parsing

def _norm_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except Exception:
        return raw


class _ArticleBody(HTMLParser):
    """Collects the article body text from a tdg.ch article page."""

    BODY_TAGS = {"p", "h2", "h3", "blockquote"}
    SKIP_TAGS = {"script", "style", "noscript", "aside", "figure", "form", "dialog"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_article = False
        self.article_depth = 0
        self.skip_depth = 0
        self.capture = None
        self.buf = []
        self.blocks = []
        self.lead = ""
        self.title = ""
        self._in_h1 = False
        self._in_lead = False

    @staticmethod
    def _classes(attrs):
        return (dict(attrs).get("class") or "")

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "article" and "data-article-body" in a:
            self.in_article = True
            self.article_depth = 1
            return
        if self.in_article:
            self.article_depth += 1
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
        if tag == "h1" and "seotitle" in cls:
            self._in_h1 = True
        if tag == "p" and "lead" in cls.split():
            self._in_lead = True
        if self.in_article and not self.skip_depth:
            is_body = ("articleParagraph" in cls or "crosshead" in cls
                       or (tag == "blockquote" and "article-element" in cls))
            if is_body:
                self.capture = tag
                self.buf = []

    def handle_endtag(self, tag):
        if self.in_article and not self.skip_depth:
            if self.capture and tag == self.capture:
                text = " ".join("".join(self.buf).split())
                if len(text) > 2:
                    self.blocks.append({"tag": tag, "text": text})
                self.capture, self.buf = None, []
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        if tag == "h1":
            self._in_h1 = False
        if tag == "p":
            self._in_lead = False
        if self.in_article:
            self.article_depth -= 1
            if self.article_depth <= 0:
                self.in_article = False
                self.article_depth = 0

    def handle_data(self, data):
        if self._in_h1 and not self.title:
            t = " ".join(data.split())
            if t:
                self.title = t
        if self._in_lead and not self.lead:
            t = " ".join(data.split())
            if t:
                self.lead = t
        if self.capture and not self.skip_depth:
            self.buf.append(data)


_OVERLAY = "Les articles ABO"


def fetch_article(target, sub=None, verbose=False):
    sub = sub or Subscriber(verbose=verbose)
    if re.fullmatch(r"\d{9,12}", str(target)):
        url = f"{SITE}/{target}"
    elif str(target).startswith("http"):
        url = str(target)
    else:
        url = SITE + "/" + str(target).lstrip("/")
    status, page = sub.get(url)
    if not page:
        return {"url": url, "status": status, "error": "no response"}
    p = _ArticleBody()
    try:
        p.feed(page)
    except Exception:
        pass
    body = "\n\n".join(b["text"] for b in p.blocks)
    meta = {}
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        stack = [data]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                if cur.get("@type") in ("NewsArticle", "Article", "ReportageNewsArticle"):
                    meta = cur
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
    title = p.title or meta.get("headline", "")
    authors = []
    for a in meta.get("author") or []:
        if isinstance(a, dict) and a.get("name"):
            authors.append(a["name"])
        elif isinstance(a, str):
            authors.append(a)
    m_id = re.search(r"-(\d{9,12})$", url) or re.search(r"/(\d{9,12})$", url)
    canon = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', page) or \
        re.search(r'<meta[^>]+property="og:url"[^>]+content="([^"]+)"', page)
    if canon and canon.group(1).startswith("http") and re.search(r"-\d{9,12}$", canon.group(1)):
        url = canon.group(1)
        m_id = re.search(r"-(\d{9,12})$", url)
    paywalled = _OVERLAY in page
    return {
        "id": m_id.group(1) if m_id else "",
        "url": url,
        "status": status,
        "title": title,
        "lead": (meta.get("description") or p.lead or "").strip(),
        "section": (meta.get("articleSection") or "") if isinstance(meta.get("articleSection"), str) else "",
        "authors": authors,
        "published": _norm_date(meta.get("datePublished")),
        "modified": _norm_date(meta.get("dateModified")),
        "is_premium": meta.get("isAccessibleForFree") is False,
        "paywalled": paywalled,
        "warning": ("paywall teaser only — the subscription token is missing or "
                    "expired (run: tdg.py token --sync)") if paywalled else "",
        "words": len(body.split()),
        "body": body,
        "paragraphs": [b["text"] for b in p.blocks],
    }


# ---------------------------------------------------------------------- output

def print_item(it):
    when = (it.get("published") or "")[:16].replace("T", " ")
    flags = []
    if it.get("is_premium"):
        flags.append("ABO")
    if it.get("is_opinion"):
        flags.append("opinion")
    if it.get("section"):
        flags.append(it["section"])
    if it.get("topics"):
        flags.append("TOPIC:" + ",".join(it["topics"]))
    head = f"[{when}] {it.get('title','')}"
    if flags:
        head += "  (" + ", ".join(flags) + ")"
    print(head)
    if it.get("lead"):
        print(f"    {it['lead']}")
    print(f"    {it.get('url','')}")


def main():
    ap = argparse.ArgumentParser(prog="tdg-news",
                                 description="Tribune de Genève CLI (RSS + subscription full text)")
    ap.add_argument("command", nargs="?", default="top",
                    help="top | search | index | article | token")
    ap.add_argument("args", nargs="*", help="count, keyword, or article id/URL(s)")
    ap.add_argument("--hours", type=int, default=0, help="only items from the last N hours")
    ap.add_argument("--feeds", default=None, help="comma-separated RSS slug override")
    ap.add_argument("--sections", default=None, help="comma-separated site sections for index "
                                                    "(empty string = front page)")
    ap.add_argument("--category", action="append", default=None,
                    help="filter index by categoryPath prefix (repeatable)")
    ap.add_argument("--pages", type=int, default=1,
                    help="index: pages per section via /<section>/page/N (default 1)")
    ap.add_argument("--only-topics", default=None,
                    help="index: keep only items tagged with these standing interests "
                         "(immobilier,taux,ai)")
    ap.add_argument("--no-sitemap", action="store_true", help="index: skip news.xml merge")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", action="store_true", help="markdown output")
    ap.add_argument("--sync", action="store_true", help="token: import refresh token from browser")
    ap.add_argument("--set", default=None, help="token: store this refresh token")
    ap.add_argument("--show", action="store_true", help="token: print the stored token")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    cmd = a.command
    want_json = a.json

    if cmd == "token":
        if a.set:
            path = save_token(a.set)
            print(f"saved to {path}")
        if a.sync:
            tok = browser_token()
            if not tok:
                print("no tdg.ch refreshToken found in any local browser profile", file=sys.stderr)
                return 1
            path = save_token(tok)
            print(f"imported browser token -> {path}")
        tok = load_token()
        if not tok:
            print("no token configured (run: tdg.py token --sync)")
            return 1
        sub = Subscriber(token=tok, verbose=a.verbose)
        ent = sub._bootstrap() or ""
        body = _jwt_body(ent)
        cv = body.get("customVars", {})
        info = {
            "token_path": TOKEN_PATH,
            "token_tail": tok[-6:],
            "hasSubscription": cv.get("hasSubscription"),
            "userType": cv.get("userType"),
            "entitlements": body.get("entitlements"),
            "token_expires_in_s": max(0, body.get("exp", 0) - int(time.time())) if body else None,
        }
        if a.show:
            info["token"] = tok
        print(json.dumps(info, indent=2, ensure_ascii=False) if want_json else
              "\n".join(f"{k}: {v}" for k, v in info.items()))
        return 0

    if cmd == "article":
        targets = a.args
        if not targets:
            print("usage: tdg.py article <id|url> [more ids]", file=sys.stderr)
            return 2
        sub = Subscriber(verbose=a.verbose)
        arts = [fetch_article(t, sub=sub, verbose=a.verbose) for t in targets]
        if want_json:
            print(json.dumps(arts if len(arts) > 1 else arts[0], ensure_ascii=False, indent=1))
            return 0
        for art in arts:
            if art.get("paywalled"):
                print("[!] paywall overlay present — token missing or expired\n", file=sys.stderr)
            print(f"# {art.get('title','')}")
            if art.get("lead"):
                print(f"\n{art['lead']}")
            print(f"\n{art.get('body','')}\n")
            print(f"— {', '.join(art.get('authors') or [])} · {art.get('published') or ''} · {art['url']}\n")
        return 0

    if cmd == "index":
        sections = None
        if a.sections is not None:
            sections = [s.strip() for s in a.sections.split(",")]
        limit = int(a.args[0]) if a.args else 0
        items, subscribed = build_index(sections=sections, hours=a.hours, limit=0,
                                        include_sitemap=not a.no_sitemap, verbose=a.verbose,
                                        pages=max(1, a.pages))
        if a.category:
            items = [i for i in items
                     if any((i.get("section") or "").startswith(c) for c in a.category)]
        if a.only_topics:
            want = {t.strip().lower() for t in a.only_topics.split(",") if t.strip()}
            items = [i for i in items if want & {t.lower() for t in i.get("topics") or []}]
        if limit:
            items = items[:limit]
        payload = {"subscriber_mode": subscribed, "count": len(items), "items": items}
        if want_json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        elif a.md:
            for i in items:
                tag = f"[{','.join(i['topics'])}] " if i.get("topics") else ""
                print(f"- {tag}[{i['title']}]({i['url']})" + (f" — {i['lead']}" if i.get("lead") else ""))
        else:
            print(f"# {len(items)} articles (subscriber_mode={subscribed})")
            for i in items:
                print_item(i)
        return 0

    # ---- public RSS commands (unchanged behaviour)
    feeds = [x.strip() for x in a.feeds.split(",") if x.strip()] if a.feeds else GENEVA_FEEDS
    items = collect(feeds)
    if a.hours:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=a.hours)).isoformat()
        items = [i for i in items if i["published"] and i["published"] >= cutoff]
    if cmd == "search":
        kw = (a.args[0] if a.args else "").lower()
        items = [i for i in items if kw in (i["title"] + " " + i["lead"]).lower()]
        n = len(items)
    else:
        n = int(a.args[0]) if a.args and a.args[0].isdigit() else 10
    items = items[:n] if n > 0 else items
    if want_json:
        print(json.dumps(items, ensure_ascii=False))
        return 0
    if a.md:
        for i in items:
            lead = (" — " + i["lead"]) if i["lead"] else ""
            print(f"- [{i['title']}]({i['url']}){lead}")
        return 0
    for i in items:
        when = i["published"][:16].replace("T", " ") if i["published"] else "n/a"
        print(f"[{when}] {i['title']} ({i['author']})")
        if i["lead"]:
            print(f"    {i['lead']}")
        print(f"    {i['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
