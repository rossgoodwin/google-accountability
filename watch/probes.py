"""Probes. Each returns {"kind": "text"|"set", "records": {key: value}}.

text: each value is compared whole; a change reports a diff.
set:  each key is an item (feed link, document); only new keys report.
A probe that raises is recorded as an error, never as a change.
"""
import hashlib
import html
import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET

import requests

from . import config

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
TIMEOUT = 30


def normalize(text):
    """Strip churn so only meaningful wording is compared."""
    noise = [re.compile(p, re.I) for p in config.NOISE_PATTERNS]
    out = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line or any(n.search(line) for n in noise):
            continue
        out.append(line)
    # Drop exact repeats while keeping order.
    seen, dedup = set(), []
    for line in out:
        if line not in seen:
            seen.add(line)
            dedup.append(line)
    return "\n".join(dedup)


def html_to_text(raw):
    raw = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|tr|section|article)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(raw)


def get(url, **kw):
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                     timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


# --------------------------------------------------------------- Google Maps

MAPS_JS = r"""
() => {
  const main = document.querySelector('[role="main"]');
  const h1 = document.querySelector('h1');
  return {
    title: document.title,
    h1: h1 ? h1.innerText : '',
    panel: main ? main.innerText.slice(0, 4000) : '',
  };
}
"""

# Earth draws inside nested shadow roots, so walk them for any visible text.
EARTH_JS = r"""
() => {
  const parts = [];
  const walk = (root, depth) => {
    if (depth > 25) return;
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
    }
    if (root.querySelectorAll) {
      for (const el of root.querySelectorAll('h1,h2,[class*="title"],[class*="name"],[class*="description"],[class*="knowledge"]')) {
        const s = (el.innerText || el.textContent || '').trim();
        if (s && s.length < 600) parts.push(s);
      }
    }
  };
  walk(document, 0);
  return {title: document.title, text: parts.join('\n').slice(0, 4000)};
}
"""


def _dismiss_consent(page):
    for label in ("Reject all", "Accept all", "I agree"):
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count():
                btn.first.click(timeout=3000)
                page.wait_for_timeout(1500)
                return
        except Exception:
            pass


def browser_probes(shots_dir):
    """Google Maps place panels and Google Earth search, in a real browser.

    Returns two probe results: maps and earth. Screenshots go to shots_dir as
    evidence (uploaded as a workflow artifact, not committed).
    """
    from playwright.sync_api import sync_playwright

    os.makedirs(shots_dir, exist_ok=True)
    maps, earth, errors = {}, {}, {}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-webgl",
                                          "--ignore-gpu-blocklist"])
        ctx = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 1400, "height": 900})
        page = ctx.new_page()

        for place, spec in config.PLACES.items():
            for q in spec["queries"]:
                for gl in config.REGIONS:
                    key = f"{place}|{q}|gl={gl}"
                    url = ("https://www.google.com/maps/search/"
                           f"{urllib.parse.quote_plus(q)}?hl=en&gl={gl}")
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        _dismiss_consent(page)
                        page.wait_for_timeout(7000)
                        data = page.evaluate(MAPS_JS)
                        maps[key + "|name"] = (data["h1"] or data["title"]).strip()
                        maps[key + "|panel"] = normalize(data["panel"])
                        page.screenshot(path=os.path.join(
                            shots_dir, f"maps_{place}_{_slug(q)}_{gl}.png"))
                    except Exception as e:
                        errors[f"maps|{key}"] = repr(e)[:300]

            q = spec["queries"][0]
            key = f"{place}|{q}"
            try:
                page.goto("https://earth.google.com/web/search/"
                          f"{urllib.parse.quote_plus(q)}", wait_until="domcontentloaded",
                          timeout=60000)
                _dismiss_consent(page)
                page.wait_for_timeout(20000)
                data = page.evaluate(EARTH_JS)
                earth[key + "|title"] = data["title"].strip()
                earth[key + "|text"] = normalize(data["text"])
                page.screenshot(path=os.path.join(shots_dir, f"earth_{place}.png"))
            except Exception as e:
                errors[f"earth|{key}"] = repr(e)[:300]
        browser.close()
    return ({"kind": "text", "records": maps},
            {"kind": "text", "records": earth},
            errors)


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ------------------------------------------------- Places API (optional key)

def places_api():
    """Google's own structured answer per region: displayName and the
    editorialSummary blurb, which is where a provenance note would most likely
    show up. Needs GOOGLE_MAPS_API_KEY (Places API (New) enabled)."""
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        return None
    records = {}
    for place, spec in config.PLACES.items():
        for q in spec["queries"]:
            for region in config.REGIONS:
                r = requests.post(
                    "https://places.googleapis.com/v1/places:searchText",
                    headers={
                        "X-Goog-Api-Key": key,
                        "X-Goog-FieldMask": ("places.id,places.displayName,"
                                             "places.formattedAddress,"
                                             "places.editorialSummary,places.types"),
                    },
                    json={"textQuery": q, "languageCode": "en", "regionCode": region,
                          "pageSize": 3},
                    timeout=TIMEOUT)
                r.raise_for_status()
                hits = r.json().get("places", [])[:3]
                slim = [{
                    "id": h.get("id"),
                    "name": (h.get("displayName") or {}).get("text"),
                    "address": h.get("formattedAddress"),
                    "summary": (h.get("editorialSummary") or {}).get("text"),
                    "types": h.get("types", [])[:4],
                } for h in hits]
                records[f"{place}|{q}|region={region}"] = json.dumps(
                    slim, indent=1, ensure_ascii=False)
    return {"kind": "text", "records": records}


# ------------------------------------------------------ official Google text

def pages():
    records = {}
    for name, url in config.PAGES.items():
        text = html_to_text(get(url).text)
        records[name] = normalize(text)[:20000]
    return {"kind": "text", "records": records}


# -------------------------------------------------------------------- feeds

def feeds():
    kws = [k.lower() for k in config.FEED_KEYWORDS]
    records = {}
    for name, url in config.FEEDS.items():
        root = ET.fromstring(get(url).content)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = html_to_text(item.findtext("description") or "")
            blob = f"{title} {desc}".lower()
            if name == "google_news" or any(k in blob for k in kws):
                records[f"{name}|{link or hashlib.sha1(title.encode()).hexdigest()}"] = title
    return {"kind": "set", "records": records}


# --------------------------------------------------------- Federal Register

def fedreg():
    records = {}
    for term in config.FEDREG_TERMS:
        r = get("https://www.federalregister.gov/api/v1/documents.json",
                params={"conditions[term]": f'"{term}"', "order": "newest",
                        "per_page": 20, "fields[]": ["title", "html_url",
                                                      "publication_date"]})
        for d in r.json().get("results", []):
            records[f"{term}|{d['html_url']}"] = f"{d['publication_date']}  {d['title']}"
    return {"kind": "set", "records": records}
