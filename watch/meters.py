"""The two numbers at the top of the status page.

Names restored for US users: four views (Google Maps and Google Earth, gulf
and lake, as seen from the US), 25 points each. Original name only scores
full, both names half, the new name only zero.

Origin explained: the two renamed places, 50 points each, for an origin note
next to the US name: wording such as "renamed" or "formerly" in the head of
the Maps place panel, or on the map or globe as read by OCR.
"""
from . import config

ORIGINAL = {"gulf": "Gulf of Mexico", "lake": "Lake Ontario"}
NEW = {"gulf": "Gulf of America", "lake": "Lake America"}
ROW_QUERY = {"gulf": "Gulf of Mexico", "lake": "Lake Ontario"}


def _score_names(text, place):
    t = (text or "").lower()
    has_old, has_new = ORIGINAL[place].lower() in t, NEW[place].lower() in t
    if has_old and not has_new:
        return 1.0
    if has_old and has_new:
        return 0.5
    return 0.0


def views(maps, earth):
    """Per-view scores, keyed for the thank-you lines."""
    out = {}
    for place in ORIGINAL:
        q = ROW_QUERY[place]
        name = maps.get(f"{place}|{q}|gl=us|name", "")
        out[f"restored|maps|{place}"] = (_score_names(name, place), name)
        seen = earth.get(f"{place}|{config.PLACES[place]['queries'][0]}|on_screen", "")
        out[f"restored|earth|{place}"] = (_score_names(seen, place), seen)
        notes = []
        for qq in config.PLACES[place]["queries"]:
            for k in (f"{place}|{qq}|gl=us|note", f"{place}|{qq}|gl=us|on_screen"):
                v = maps.get(k, "")
                if k.endswith("|note") and v and v != "none":
                    notes.append(v)
                elif k.endswith("|on_screen") and "word:" in v:
                    notes.append(v)
        if "word:" in earth.get(f"{place}|{config.PLACES[place]['queries'][0]}|on_screen", ""):
            notes.append("globe")
        out[f"explained|{place}"] = (1.0 if notes else 0.0, name)
    return out


def totals(v):
    restored = sum(s for k, (s, _) in v.items() if k.startswith("restored|")) / 4 * 100
    explained = sum(s for k, (s, _) in v.items() if k.startswith("explained|")) / 2 * 100
    return int(restored + 0.5), int(explained + 0.5)


def thanks_lines(old_views, new_views, date):
    """Plain dated lines for every view that improved."""
    lines = []
    for k, (score, shown) in new_views.items():
        before = old_views.get(k, (0.0, ""))[0] if old_views else None
        if before is None or score <= before:
            continue
        kind, *rest = k.split("|")
        place = rest[-1]
        if kind == "restored":
            where = "Google Maps" if rest[0] == "maps" else "Google Earth"
            lines.append(f"{date}: {where} now shows US users “{shown}”. Thank you.")
        else:
            lines.append(f"{date}: Google Maps now tells US users where the name "
                         f"“{NEW[place]}” comes from. Thank you.")
    return lines
