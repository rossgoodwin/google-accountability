"""Build the status page into site/ from snapshots/ and this run's screenshots.

Usage: python -m watch.site
Deployed by the workflow to GitHub Pages after every run.
"""
import datetime as dt
import html
import json
import os
import shutil

from . import config

SNAP = "snapshots"
SHOTS = os.path.join("out", "screens")
OUT = "site"

REGIONS = [("us", "United States"), ("ca", "Canada"), ("mx", "Mexico"),
           ("gb", "Elsewhere (checked from the UK)")]
# Query each table row uses: the older, internationally recognised name.
ROW_QUERY = {"gulf": "Gulf of Mexico", "lake": "Lake Ontario"}
TITLES = {"gulf": "Gulf of Mexico", "lake": "Lake Ontario"}
RENAMED = {"gulf": "February 2025", "lake": "August 2026"}
# Screenshots shown on the page: (file in out/screens, caption, crop map area)
IMAGES = [
    ("maps_gulf_gulf-of-mexico_us.png", "Google Maps, United States view", True),
    ("maps_gulf_gulf-of-mexico_mx.png", "Google Maps, Mexico view", True),
    ("earth_gulf_1.png", "Google Earth", False),
    ("maps_lake_lake-ontario_us.png", "Google Maps, United States view", True),
    ("maps_lake_lake-ontario_ca.png", "Google Maps, Canada view", True),
    ("earth_lake_1.png", "Google Earth", False),
]
SOURCE_LABELS = {"maps": "Google Maps", "earth": "Google Earth", "places": "Places API",
                 "pages": "Google's own pages", "feeds": "News and blog",
                 "fedreg": "Federal Register"}

e = html.escape


def load(name, default):
    try:
        with open(os.path.join(SNAP, name), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


IMAGES_ON = True


def picture(fname, crop):
    """Copy a screenshot into the site as a JPEG. Returns its path or None."""
    src = os.path.join(SHOTS, fname)
    if not IMAGES_ON or not os.path.exists(src):
        return None
    try:
        from PIL import Image
        img = Image.open(src).convert("RGB")
        if crop:
            img = img.crop((440, 72, img.width, img.height))
        img.thumbnail((1100, 1100))
        out = os.path.join("img", fname[:-4] + ".jpg")
        img.save(os.path.join(OUT, out), quality=82)
        return out
    except Exception:
        return None


def pretty_key(source, key):
    parts = key.split("|")
    if source == "maps" and len(parts) >= 4:
        region = dict(REGIONS).get(parts[2].replace("gl=", ""), parts[2])
        what = {"name": "place name", "panel": "place panel",
                "on_screen": "label on the map"}.get(parts[3], parts[3])
        return f"{TITLES.get(parts[0], parts[0])}, {region}, {what}"
    if source == "earth" and len(parts) >= 3:
        return f"{TITLES.get(parts[0], parts[0])}, label on the globe"
    if source == "feeds":
        return parts[1] if len(parts) > 1 else key
    return key.replace("|", ", ")


def build(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "img"))

    mstate = load("_meters.json", {})
    restored, explained = mstate.get("restored", 0), mstate.get("explained", 0)
    thanks = mstate.get("thanks", [])
    thanks_html = ("<ul class=thanks>" + "".join(f"<li>{e(t)}</li>" for t in reversed(thanks)) + "</ul>"
                   if thanks else "")
    maps = load("maps.json", {})
    earth = load("earth.json", {})
    health = load("_health.json", {})
    events = load("_events.json", [])

    notice = dt.date.fromisoformat(config.NOTICE_DATE)
    days = (now.date() - notice).days
    since = [ev for ev in events if ev["time"][:10] >= config.NOTICE_DATE]
    notes = [ev for ev in since if ev.get("provenance")]

    # Places.
    blocks = []
    for place, title in TITLES.items():
        q = ROW_QUERY[place]
        rows = []
        for gl, label in REGIONS:
            name = maps.get(f"{place}|{q}|gl={gl}|name", "")
            rows.append(f"<tr><th scope=row>{e(label)}</th>"
                        f"<td>{e(name) or '<span class=dim>not read this run</span>'}</td></tr>")
        globe = earth.get(f"{place}|{config.PLACES[place]['queries'][0]}|on_screen", "")
        if globe and globe not in ("none", "ocr unavailable"):
            globe_html = f"<p>Google Earth, viewed from the United States, labels it <strong>{e(globe)}</strong>.</p>"
        else:
            globe_html = "<p class=dim>Google Earth: label not read this run.</p>"
        pics = []
        for fname, cap, crop in IMAGES:
            if not fname.split("_")[1] == place:
                continue
            src = picture(fname, crop)
            if src:
                pics.append(f'<figure><a href="{src}"><img src="{src}" alt="{e(cap)} of {e(title)}" loading=lazy></a>'
                            f"<figcaption>{e(cap)}</figcaption></figure>")
        blocks.append(f"""
<section>
  <h2>{e(title)}</h2>
  <p class=sub>Renamed for US users in {RENAMED[place]}.</p>
  <table>
    <thead><tr><th>Seen from</th><th>Google Maps calls it</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  {globe_html}
  <div class=pics>{''.join(pics)}</div>
</section>""")

    # Changes.
    items = []
    for ev in reversed(events[-30:]):
        src = SOURCE_LABELS.get(ev["source"], ev["source"])
        what = pretty_key(ev["source"], ev["key"])
        if ev["old"] is None:
            body = e(what)
            if ev["source"] == "feeds" and ev.get("new", "").startswith("http"):
                body = f'<a href="{e(ev["new"])}">{e(what)}</a>'
            change = f"New: {body}"
        else:
            old = ev["old"] if len(ev["old"]) < 120 else ev["old"][:117] + "..."
            new = ev["new"] if len(ev["new"]) < 120 else ev["new"][:117] + "..."
            change = f"{e(what)}: <s>{e(old)}</s> → {e(new)}"
        flag = " <strong class=flag>possible provenance note</strong>" if ev.get("provenance") else ""
        items.append(f"<li><time>{e(ev['time'])}</time> <span class=src>{e(src)}</span> {change}{flag}</li>")
    changes = (f"<ul class=changes>{''.join(items)}</ul>" if items
               else "<p class=dim>No changes recorded yet. Watching began on 26 November 2026.</p>")

    failing = [k for k, n in health.items() if n >= 2]
    health_line = ("All sources answered on the last check." if not failing else
                   f"{len(failing)} source(s) failing: {', '.join(e(k) for k in failing)}.")

    if explained >= 100:
        note_line = "Both renamed labels now say where the name came from."
    elif explained > 0:
        note_line = "One of the two renamed labels now says where the name came from."
    else:
        note_line = "None detected in any view this page checks."

    page = f"""<!DOCTYPE html>
<html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<meta http-equiv=refresh content=300>
<link rel=preconnect href="https://fonts.googleapis.com">
<link rel=preconnect href="https://fonts.gstatic.com" crossorigin>
<link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Cousine:ital,wght@0,400;0,700;1,400&family=Inter+Tight:wght@700;800&display=swap">
<title>Google Accountability</title>
<meta name=description content="What Google Maps and Google Earth call the Gulf of Mexico and Lake Ontario in each country, checked about every 15 minutes.">
<style>
:root {{ --bg:#fbfaf7; --fg:#1b1b1a; --dim:#6b6a66; --line:#dedbd3; --accent:#a3271b; --card:#fff; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#141413; --fg:#ecebe6; --dim:#9a988f; --line:#33322e; --accent:#e5735f; --card:#1d1d1b; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.65 Cousine, "Courier New", monospace; }}
main {{ max-width:880px; margin:0 auto; padding:48px 20px 80px; }}
h1, h2, h3 {{ font-family:"Inter Tight", "Helvetica Neue", Arial, sans-serif; font-weight:800; letter-spacing:-0.015em; }}
h1 {{ font-size:2.6rem; line-height:1.05; margin:0 0 14px; }}
h2 {{ font-size:1.6rem; margin:0; }}
h3 {{ font-size:1.2rem; margin:0 0 10px; }}
p {{ margin:0 0 14px; }}
a {{ color:var(--accent); }}
.subtitle {{ font-size:1rem; max-width:70ch; margin:0 0 4px; }}
.subtitle a {{ white-space:nowrap; }}
.lede {{ font-size:1rem; max-width:68ch; margin-top:36px; }}
.meters {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; margin:26px 0 12px; }}
.meter {{ background:var(--card); border:1px solid var(--line); padding:22px 22px 18px; }}
.meter b {{ display:block; font:800 5rem/1 "Inter Tight", "Helvetica Neue", Arial, sans-serif; font-variant-numeric:tabular-nums; letter-spacing:-0.03em; }}
.meter .label {{ display:block; font:700 1rem/1.3 Cousine, "Courier New", monospace; margin:10px 0 12px; }}
.bar {{ height:8px; background:var(--line); margin:0 0 14px; }}
.bar i {{ display:block; height:100%; background:var(--accent); }}
.meter p {{ font-size:.85rem; color:var(--dim); margin:0; }}
.meternote {{ font-size:.85rem; color:var(--dim); }}
.thanks {{ list-style:none; padding:0; margin:8px 0 0; }}
.thanks li {{ padding:10px 0; border-bottom:1px solid var(--line); }}
.dim, .sub {{ color:var(--dim); }}
.sub {{ margin:2px 0 14px; font-size:.95rem; }}
.count {{ display:flex; gap:22px; align-items:baseline; flex-wrap:wrap;
  border-top:2px solid var(--fg); border-bottom:1px solid var(--line);
  padding:18px 0; margin:28px 0 40px; }}
.count b {{ font:800 3.4rem/1 "Inter Tight", "Helvetica Neue", Arial, sans-serif; font-variant-numeric:tabular-nums; }}
.count div {{ max-width:52ch; }}
section {{ margin:0 0 48px; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; line-height:1.45; margin:0 0 14px; }}
th, td {{ text-align:left; padding:9px 10px 9px 0; border-bottom:1px solid var(--line); vertical-align:top; }}
thead th {{ font-weight:700; color:var(--dim); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
tbody th {{ font-weight:700; width:42%; }}
.pics {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(250px, 1fr)); gap:14px; }}
figure {{ margin:0; background:var(--card); border:1px solid var(--line); }}
figure img {{ display:block; width:100%; aspect-ratio:4/3; object-fit:cover; }}
figcaption {{ font-size:12px; color:var(--dim); padding:7px 9px; }}
.changes {{ list-style:none; padding:0; margin:0; font-size:13px; line-height:1.5; }}
.changes li {{ padding:9px 0; border-bottom:1px solid var(--line); }}
.changes time {{ color:var(--dim); font-variant-numeric:tabular-nums; margin-right:6px; }}
.src {{ font-weight:700; margin-right:6px; }}
.flag {{ color:var(--accent); }}
footer {{ border-top:1px solid var(--line); margin-top:48px; padding-top:18px; font-size:.85rem; color:var(--dim); }}
@media (max-width:640px) {{ .meters {{ grid-template-columns:1fr; }} }}
@media (max-width:560px) {{ h1 {{ font-size:2.1rem; }} tbody th {{ width:auto; }} .count b {{ font-size:2.6rem; }} .meter b {{ font-size:4rem; }} }}
</style>
</head>
<body>
<main>
<h1>Google Accountability</h1>
<p class=subtitle>An open-source bot that checks what Google Maps and Google Earth call the Gulf of Mexico and Lake Ontario in four countries, about every 15 minutes, and keeps a public record of every change. <a href="{config.REPO_URL}">Code and full record on GitHub</a></p>

<div class=meters>
  <div class=meter>
    <b>{restored}%</b>
    <span class=label>Names restored for US users</span>
    <div class=bar><i style="width:{restored}%"></i></div>
    <p>How much of what Google Maps and Google Earth show people in the United States uses the original names, Gulf of Mexico and Lake Ontario. A label showing both names counts as halfway.</p>
  </div>
  <div class=meter>
    <b>{explained}%</b>
    <span class=label>Origin explained</span>
    <div class=bar><i style="width:{explained}%"></i></div>
    <p>How many of the two renamed labels tell US users where the new name came from, with a note beside the name in Maps or on the map itself.</p>
  </div>
</div>
<p class=meternote>Both numbers come from what Google itself shows, checked about every 15 minutes. When either one rises, a dated thank-you appears here.</p>
{thanks_html}

<p class=lede>Google renamed two bodies of water for its users in the United States after US executive orders. Its maps there show only the new names. This page records what Google Maps and Google Earth show in each country, checked about every 15 minutes.</p>

<div class=count>
  <b>{days}</b>
  <div>{'day' if days == 1 else 'days'} since Google was asked to add a note to these labels saying where the new names come from.<br>
  <strong>Note on the labels:</strong> {note_line}</div>
</div>

{''.join(blocks)}

<section>
  <h3>Changes</h3>
  {changes}
</section>

<footer>
  <p>Last checked {e(now.strftime('%d %B %Y, %H:%M UTC'))}. {health_line}</p>
  <p>Sources: Google Maps from four countries, Google Earth, Google's own blog and help pages, Google News and the Federal Register. Google Earth draws its labels as pictures, so they are read from screenshots by OCR. Every check is committed to <a href="{config.REPO_URL}">the repository</a>, whose history is the full record. The code is open source under the <a href="{config.REPO_URL}/blob/main/LICENSE">MIT License</a>.</p>
</footer>
</main>
</body>
</html>
"""
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    return os.path.join(OUT, "index.html")


def build_fallback():
    """A copy of the page without screenshots at the repo root, for when Pages
    publishes straight from the branch instead of from the workflow. Images
    stay out so the repo doesn't grow by megabytes a day."""
    global IMAGES_ON
    IMAGES_ON = False
    try:
        path = build()
    finally:
        IMAGES_ON = True
    shutil.copy(path, "index.html")
    return "index.html"


if __name__ == "__main__":
    import sys
    if "--fallback" in sys.argv:
        print(build_fallback())
    print(build())
