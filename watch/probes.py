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


PANEL_BODY_STARTS = {"details", "quick facts", "overview", "about", "reviews", "about this data"}


def note_words(panel_text):
    """Origin-note wording in the head of a place panel, e.g. "renamed",
    "formerly". Returns a sorted list as text, or "none"."""
    head = []
    for line in panel_text.splitlines():
        if line.strip().lower() in PANEL_BODY_STARTS:
            break
        head.append(line)
    flat = " ".join(head).lower()
    found = sorted(w for w in config.PROVENANCE_WORDS if w.lower() in flat)
    return ", ".join(found) or "none"


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
                        # Any origin note sits beside the name, above the
                        # panel's encyclopedia text, so only that header is read.
                        maps[key + "|note"] = note_words(data["h1"] + "\n" + data["panel"])
                        shot = os.path.join(shots_dir,
                                            f"maps_{place}_{_slug(q)}_{gl}.png")
                        page.screenshot(path=shot)
                        # OCR only the map itself: the search box would echo
                        # the query and the side panel is read from the DOM.
                        seen = _ocr_names(_map_only(shot), place)
                        # Reading no name at all is a failed reading, not a
                        # change: keep the last value. Some map views never
                        # show a readable label, so misses here aren't errors.
                        if seen != "ocr unavailable":
                            maps[key + "|on_screen"] = seen
                    except Exception as e:
                        errors[f"maps|{key}"] = repr(e)[:300]

            q = spec["queries"][0]
            key = f"{place}|{q}"
            try:
                # Earth's search ignores the query from a cold start, so fly
                # the camera to the place directly, from more than one height.
                lat, lon, dists = spec["earth"]
                seen, rendered = set(), False
                for n, dist in enumerate(dists):
                    page.goto("https://earth.google.com/web/"
                              f"@{lat},{lon},0a,{dist}d,35y,0h,0t,0r",
                              wait_until="domcontentloaded", timeout=60000)
                    _dismiss_consent(page)
                    page.wait_for_timeout(15000)
                    # Close the "New from Google Earth" promo, which is drawn
                    # on canvas and has no DOM button.
                    for _ in range(3):
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(1500)
                    page.wait_for_timeout(12000)
                    _click_text_buttons(page)
                    page.wait_for_timeout(3000)
                    data = page.evaluate(EARTH_JS)
                    rendered = rendered or bool(data["canvases"])
                    _debug(f"earth_dom_{place}_{n}", f"url={page.url}\n{data['text']}")
                    shot = os.path.join(shots_dir, f"earth_{place}_{n}.png")
                    page.screenshot(path=shot)
                    _debug_image(shot)
                    found = _ocr_names(shot, place)
                    if found not in ("none", "ocr unavailable"):
                        seen.update(found.split(", "))
                earth[key + "|rendered"] = "canvas" if rendered else "no canvas"
                earth[key + "|on_screen"] = ", ".join(sorted(seen)) or "none"
            except Exception as e:
                errors[f"earth|{key}"] = repr(e)[:300]
        browser.close()
    return ({"kind": "text", "records": maps},
            {"kind": "text", "records": earth},
            errors)


def _tesseract(path):
    return subprocess.run(["tesseract", path, "stdout", "--psm", "11"],
                          capture_output=True, text=True, timeout=120).stdout


def _ocr_text(path):
    """OCR the screenshot as-is, then again enhanced for map labels: Earth
    draws water names as pale italic text on dark blue, which plain OCR
    misses. The enhanced passes upscale the middle of the frame and pull the
    light text out as black on white."""
    try:
        texts = [_tesseract(path)]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    try:
        from PIL import Image, ImageOps
        img = Image.open(path).convert("RGB")
        w, h = img.size
        crops = [img, img.crop((w // 6, h // 6, w * 5 // 6, h * 5 // 6))]
        for i, crop in enumerate(crops):
            big = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
            gray = ImageOps.grayscale(big)
            light = gray.point(lambda v: 0 if v > 150 else 255)    # light text -> black
            for j, variant in enumerate([ImageOps.autocontrast(gray), light]):
                tmp = f"{path}.ocr{i}{j}.png"
                variant.save(tmp)
                try:
                    texts.append(_tesseract(tmp))
                finally:
                    os.remove(tmp)
    except Exception:
        pass
    return "\n".join(texts)


def _debug(name, text):
    """With LABEL_WATCH_DEBUG set, dump raw text to snapshots/_debug/ (never
    compared) so a human can see what a probe actually saw."""
    if os.environ.get("LABEL_WATCH_DEBUG"):
        os.makedirs("snapshots/_debug", exist_ok=True)
        with open(f"snapshots/_debug/{name}.txt", "w", encoding="utf-8") as f:
            f.write(text or "")


def _click_text_buttons(page, words=("Dismiss", "Got it", "No thanks", "Close", "Skip")):
    """Earth draws its promos on canvas, so find their buttons by OCR and
    click them. Returns how many it clicked."""
    tmp = "/tmp/_earth_buttons.png"
    clicked = 0
    for _ in range(3):
        page.screenshot(path=tmp)
        hit = None
        # Pass 1 as-is; pass 2 upscaled and thresholded, since button text is
        # often light blue on white, which plain OCR skips.
        for scale in (1, 2):
            src = tmp
            if scale == 2:
                try:
                    from PIL import Image, ImageOps
                    g = ImageOps.grayscale(Image.open(tmp))
                    g = g.resize((g.width * 2, g.height * 2), Image.LANCZOS)
                    g.point(lambda v: 0 if v < 200 else 255).save(tmp + ".2.png")
                    src = tmp + ".2.png"
                except Exception:
                    break
            try:
                tsv = subprocess.run(["tesseract", src, "stdout", "--psm", "11", "tsv"],
                                     capture_output=True, text=True, timeout=120).stdout
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return clicked
            for row in tsv.splitlines()[1:]:
                f = row.split("\t")
                if len(f) == 12 and f[11].strip().strip(".,") in words:
                    x, y, w, h = map(int, f[6:10])
                    hit = ((x + w / 2) / scale, (y + h / 2) / scale)
                    break
            if hit:
                break
        if not hit:
            return clicked
        page.mouse.click(*hit)
        clicked += 1
        page.wait_for_timeout(2000)
    return clicked


def _debug_image(path):
    if os.environ.get("LABEL_WATCH_DEBUG"):
        try:
            from PIL import Image
            os.makedirs("snapshots/_debug", exist_ok=True)
            img = Image.open(path).convert("RGB")
            img.thumbnail((1000, 1000))
            img.save(f"snapshots/_debug/{os.path.basename(path)[:-4]}.jpg", quality=80)
        except Exception:
            pass


def _ocr_names(path, place=None):
    """Read a screenshot with tesseract and report which watched names and
    provenance words appear on screen. Only presence is compared, since raw
    OCR text wobbles from run to run."""
    txt = _ocr_text(path)
    _debug("ocr_" + os.path.basename(path), txt)
    if txt is None:
        return "ocr unavailable"
    flat = re.sub(r"\s+", " ", txt).lower()
    specs = [config.PLACES[place]] if place else config.PLACES.values()
    names = {n for spec in specs for n in spec["names"]}
    found = sorted(n for n in names if n.lower() in flat)
    found += sorted(f"word:{w}" for w in config.PROVENANCE_WORDS if w.lower() in flat)
    return ", ".join(found) or "none"


def _map_only(path, left=440, top=72):
    try:
        from PIL import Image
        img = Image.open(path)
        out = path[:-4] + "_map.png"
        img.crop((left, top, img.width, img.height)).save(out)
        return out
    except Exception:
        return path


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

def _recent(item, days=14):
    from email.utils import parsedate_to_datetime
    import datetime as dt
    raw = item.findtext("pubDate")
    if not raw:
        return True
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc) - when < dt.timedelta(days=days)


def feeds():
    """New items only. Google News reshuffles which older articles it returns
    and rewrites their links, so items are keyed by title and anything older
    than two weeks is ignored."""
    kws = [k.lower() for k in config.FEED_KEYWORDS]
    records = {}
    for name, url in config.FEEDS.items():
        root = ET.fromstring(get(url).content)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = html_to_text(item.findtext("description") or "")
            blob = f"{title} {desc}".lower()
            if not _recent(item):
                continue
            if name == "google_news" or any(k in blob for k in kws):
                records[f"{name}|{title or link}"] = link
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


# ------------------------------------------------------------------ GOOG

STOCK_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/GOOG"
             "?range=3mo&interval=1d&includePrePost=false")


def parse_stock(data, fetched_at):
    """Yahoo chart JSON -> what the page needs. Raises on anything missing."""
    res = data["chart"]["result"][0]
    meta = res["meta"]
    closes = res["indicators"]["quote"][0]["close"]
    days = {}
    import datetime as dt
    for ts, c in zip(res["timestamp"], closes):
        if c is not None:
            d = dt.datetime.fromtimestamp(ts + meta.get("gmtoffset", 0), dt.timezone.utc).date()
            days[d.isoformat()] = round(c, 2)
    return {
        "symbol": meta.get("symbol", "GOOG"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName", "Nasdaq"),
        "currency": meta.get("currency", "USD"),
        "price": round(meta["regularMarketPrice"], 2),
        "previous_close": round(meta.get("chartPreviousClose") or meta.get("previousClose"), 2)
        if (meta.get("chartPreviousClose") or meta.get("previousClose")) else None,
        "market_time": meta["regularMarketTime"],
        "fetched_at": fetched_at,
        "daily_closes": dict(sorted(days.items())[-90:]),
    }


def _stooq(fetched_at):
    """Stooq's free CSVs: latest quote plus daily history. Times are Polish."""
    import csv, io, datetime as dt
    from zoneinfo import ZoneInfo
    q = list(csv.DictReader(io.StringIO(
        get("https://stooq.com/q/l/?s=goog.us&f=sd2t2ohlcv&h&e=csv").text)))[0]
    when = dt.datetime.strptime(f"{q['Date']} {q['Time']}", "%Y-%m-%d %H:%M:%S")
    when = when.replace(tzinfo=ZoneInfo("Europe/Warsaw"))
    hist = csv.DictReader(io.StringIO(get("https://stooq.com/q/d/l/?s=goog.us&i=d").text))
    days = {r["Date"]: round(float(r["Close"]), 2) for r in hist if r.get("Close")}
    return {"symbol": "GOOG", "exchange": "Nasdaq", "currency": "USD",
            "price": round(float(q["Close"]), 2), "market_time": int(when.timestamp()),
            "fetched_at": fetched_at, "source": "Stooq",
            "daily_closes": dict(sorted(days.items())[-90:])}


def _money(s):
    return float(re.sub(r"[^0-9.\-]", "", str(s)))


def _nasdaq(fetched_at):
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
         "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
    r = requests.get("https://api.nasdaq.com/api/quote/GOOG/info?assetclass=stocks",
                     headers=h, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()["data"]["primaryData"]
    price = _money(d["lastSalePrice"])
    prev = round(price - _money(d["netChange"]), 2) if d.get("netChange") not in (None, "", "UNCH") else price
    import datetime as dt
    from zoneinfo import ZoneInfo
    stamp = re.sub(r"^DATA AS OF\s*", "", d.get("lastTradeTimestamp", ""), flags=re.I)
    stamp = re.sub(r"\s*ET$", "", stamp).strip()
    when = None
    for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y"):
        try:
            when = dt.datetime.strptime(stamp, fmt).replace(tzinfo=ZoneInfo("America/New_York"))
            break
        except ValueError:
            pass
    when = when or dt.datetime.now(dt.timezone.utc)
    return {"price": round(price, 2), "previous_close": prev,
            "market_time": int(when.timestamp()), "source": "Nasdaq"}


def _cnbc(fetched_at):
    r = requests.get("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
                     "?symbols=GOOG&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json",
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    q = r.json()["FormattedQuoteResult"]["FormattedQuote"][0]
    import datetime as dt
    when = dt.datetime.strptime(q["last_time"][:19], "%Y-%m-%dT%H:%M:%S")
    from zoneinfo import ZoneInfo
    when = when.replace(tzinfo=ZoneInfo("America/New_York"))
    return {"price": round(_money(q["last"]), 2),
            "previous_close": round(_money(q["previous_day_closing"]), 2),
            "market_time": int(when.timestamp()), "source": "CNBC"}


def _google_finance(fetched_at):
    html_ = get("https://www.google.com/finance/quote/GOOG:NASDAQ?hl=en").text
    price = re.search(r'data-last-price="([0-9.]+)"', html_)
    ts = re.search(r'data-last-normal-market-timestamp="([0-9]+)"', html_)
    prev = re.search(r'Previous close</div>.*?\$([0-9,.]+)', html_, re.S)
    if not price:
        raise ValueError("no price on the page")
    import time as _t
    return {"price": round(float(price.group(1)), 2),
            "previous_close": round(_money(prev.group(1)), 2) if prev else None,
            "market_time": int(ts.group(1)) if ts else int(_t.time()),
            "source": "Google Finance"}


def stock(fetched_at):
    """GOOG from the first source that answers. Each returns at least price,
    market_time and source; Yahoo and Stooq also bring daily history."""
    problems = []
    for url in (STOCK_URL, STOCK_URL.replace("query1", "query2")):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"},
                             timeout=TIMEOUT)
            r.raise_for_status()
            out = parse_stock(r.json(), fetched_at)
            out["source"] = "Yahoo Finance"
            return out
        except Exception as e:
            problems.append(f"yahoo: {e!r}"[:160])
    for fn in (_nasdaq, _cnbc, _google_finance, _stooq):
        try:
            out = fn(fetched_at)
            out.setdefault("symbol", "GOOG")
            out.setdefault("exchange", "Nasdaq")
            out.setdefault("currency", "USD")
            out.setdefault("daily_closes", {})
            out["fetched_at"] = fetched_at
            return out
        except Exception as e:
            problems.append(f"{fn.__name__.strip('_')}: {e!r}"[:160])
    raise RuntimeError(" | ".join(problems))
