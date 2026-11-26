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

import subprocess

import requests

from . import config

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
TIMEOUT = 30


def normalize(text):
    """Strip churn so only meaningful wording is compared."""
    noise = [re.compile(p, re.I) for p in config.NOISE_PATTERNS]
    out = []
    text = re.sub(r"[\ue000-\uf8ff]", "", text)             # icon-font glyphs
    text = re.sub(r"\b\d\.\d\s*\(\d[\d,.]*[KkMm]?\)", "", text)  # 4.6(1,606) ratings
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
  const seen = new Set();
  let canvases = 0;
  const walk = (root, depth) => {
    if (depth > 30) return;
    for (const el of root.querySelectorAll('*')) {
      if (el.tagName === 'CANVAS') canvases++;
      if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
      if (['SCRIPT', 'STYLE', 'TEMPLATE'].includes(el.tagName)) continue;
      for (const n of el.childNodes) {
        if (n.nodeType !== 3) continue;
        const t = n.textContent.replace(/\s+/g, ' ').trim();
        if (t && t.length < 400 && !seen.has(t)) { seen.add(t); parts.push(t); }
      }
    }
  };
  walk(document, 0);
  return {title: document.title, canvases, text: parts.join('\n').slice(0, 6000)};
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
                        first = page.locator('[role="feed"] a[href*="/maps/place/"]')
                        if first.count():
                            first.first.click(timeout=10000)
                            page.wait_for_timeout(6000)
                        data = page.evaluate(MAPS_JS)
                        maps[key + "|name"] = (data["h1"] or data["title"]).strip()
                        # Google sometimes serves bots a cut-down panel. Skip
                        # it rather than report it; the last full panel stands.
                        if "limited view of google maps" not in data["panel"].lower():
                            maps[key + "|panel"] = normalize(data["panel"])
                        shot = os.path.join(shots_dir,
                                            f"maps_{place}_{_slug(q)}_{gl}.png")
                        page.screenshot(path=shot)
                        maps[key + "|on_screen"] = _ocr_names(shot)
                    except Exception as e:
                        errors[f"maps|{key}"] = repr(e)[:300]

            q = spec["queries"][0]
            key = f"{place}|{q}"
            try:
                page.goto("https://earth.google.com/web/search/"
                          f"{urllib.parse.quote_plus(q)}", wait_until="domcontentloaded",
                          timeout=60000)
                _dismiss_consent(page)
                page.wait_for_timeout(30000)
                # Earth's web app is Flutter, drawn on canvas, so its text is
                # not in the page. Read the screenshot instead.
                data = page.evaluate(EARTH_JS)
                earth[key + "|rendered"] = "canvas" if data["canvases"] else "no canvas"
                shot = os.path.join(shots_dir, f"earth_{place}.png")
                page.screenshot(path=shot)
                earth[key + "|on_screen"] = _ocr_names(shot)
            except Exception as e:
                errors[f"earth|{key}"] = repr(e)[:300]
        browser.close()
    return ({"kind": "text", "records": maps},
            {"kind": "text", "records": earth},
            errors)


def _ocr_names(path):
    """Read a screenshot with tesseract and report which watched names and
    provenance words appear on screen. Only presence is compared, since raw
    OCR text wobbles from run to run."""
    try:
        txt = subprocess.run(["tesseract", path, "stdout", "--psm", "11"],
                             capture_output=True, text=True, timeout=120).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "ocr unavailable"
    flat = re.sub(r"\s+", " ", txt).lower()
    names = {n for spec in config.PLACES.values() for n in spec["names"]}
    found = sorted(n for n in names if n.lower() in flat)
    found += sorted(f"word:{w}" for w in config.PROVENANCE_WORDS if w.lower() in flat)
    return ", ".join(found) or "none"


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
