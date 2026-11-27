"""Run every probe, compare with the last snapshot, write a report.

Usage: python -m watch.run [--only maps,earth,places,pages,feeds,fedreg]

Writes snapshots/<source>.json (committed, so git history is the record),
snapshots/_health.json (failure streaks), and out/report.md when anything
worth telling Ross about happened. Exit code is always 0 unless the bot
itself crashes; "changed" is signalled through $GITHUB_OUTPUT.
"""
import argparse
import datetime as dt
import difflib
import json
import os
import sys

from . import config, probes

SNAP = "snapshots"
OUT = "out"


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def compare(kind, old, new):
    """Return a list of (key, old_value, new_value) worth reporting.

    old is None on the first run for a source: that run is the baseline and
    reports nothing. For text sources a key that vanished this run (say one
    page failed to load) keeps its old value rather than reporting a removal.
    """
    if old is None:
        return []
    changes = []
    if kind == "set":
        for k, v in new.items():
            if k not in old:
                changes.append((k, None, v))
    else:
        for k, v in new.items():
            if k not in old:
                continue  # new key: baseline it quietly
            if old[k] != v:
                changes.append((k, old[k], v))
    return changes


def merge(kind, old, new):
    if old is None:
        return dict(new)
    merged = dict(old)
    merged.update(new)
    if kind == "set" and len(merged) > 2000:  # keep feeds from growing forever
        merged = dict(sorted(merged.items())[-2000:])
    return merged


def provenance_hits(text):
    t = (text or "").lower()
    return [w for w in config.PROVENANCE_WORDS if w.lower() in t]


def render_change(source, key, old, new):
    lines = [f"#### `{source}` · `{key}`"]
    if old is None:
        lines.append(f"New: {new}")
        return "\n".join(lines)
    hits_new = set(provenance_hits(new)) - set(provenance_hits(old))
    if hits_new:
        lines.append(f"**Possible provenance language appeared:** {', '.join(sorted(hits_new))}")
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(), "before", "after",
                                lineterm="", n=2)
    body = "\n".join(list(diff)[:120])
    lines.append("```diff\n" + body + "\n```")
    return "\n".join(lines)


def run(only=None, shots_dir=os.path.join(OUT, "screens")):
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    results, errors = {}, {}

    def want(name):
        return not only or name in only

    if want("maps") or want("earth"):
        try:
            maps, earth, errs = probes.browser_probes(shots_dir)
            if want("maps"):
                results["maps"] = maps
            if want("earth"):
                results["earth"] = earth
            errors.update(errs)
        except Exception as e:
            errors["browser"] = repr(e)[:300]

    for name, fn in [("places", probes.places_api), ("pages", probes.pages),
                     ("feeds", probes.feeds), ("fedreg", probes.fedreg)]:
        if not want(name):
            continue
        try:
            r = fn()
            if r is not None:
                results[name] = r
        except Exception as e:
            errors[name] = repr(e)[:300]

    # Compare and store. OCR readings ("|on_screen") can flicker, so a change
    # there has to show up on two runs in a row before it counts.
    pending = load(os.path.join(SNAP, "_pending.json"), {})
    sections = []
    for source, r in results.items():
        path = os.path.join(SNAP, f"{source}.json")
        old = load(path, None)
        records = dict(r["records"])
        changes = []
        for k, o, v in compare(r["kind"], old, records):
            pk = f"{source}|{k}"
            if k.endswith("|on_screen") and pending.get(pk) != v:
                pending[pk] = v
                records[k] = o          # hold the old reading for now
                continue
            pending.pop(pk, None)
            changes.append((k, o, v))
        for k, v in r["records"].items():  # a flicker that settled back clears
            pk = f"{source}|{k}"
            if pk in pending and old and old.get(k) == v:
                pending.pop(pk)
        save(path, merge(r["kind"], old, records))
        if changes:
            sections.append((source, changes))
    save(os.path.join(SNAP, "_pending.json"), pending)

    # Health: a source that keeps failing means the bot has gone blind there.
    health = load(os.path.join(SNAP, "_health.json"), {})
    blind = []
    for k in set(health) | set(errors):
        if k in errors:
            health[k] = health.get(k, 0) + 1
            if health[k] == config.BLIND_AFTER:
                blind.append((k, errors[k]))
        else:
            health.pop(k, None)
    save(os.path.join(SNAP, "_health.json"), health)

    # Report.
    os.makedirs(OUT, exist_ok=True)
    report_path = os.path.join(OUT, "report.md")
    changed = bool(sections or blind)
    if changed:
        order = ["maps", "places", "earth", "pages", "fedreg", "feeds"]
        sections.sort(key=lambda s: order.index(s[0]) if s[0] in order else 99)
        n = sum(len(c) for _, c in sections)
        head = [f"Run at {now}.", ""]
        label_changes = [c for s, cs in sections if s in ("maps", "places", "earth")
                         for c in cs]
        if label_changes:
            head.append(f"**{len(label_changes)} change(s) on Google's own place data.** "
                        "Screenshots from this run are attached to the workflow run.")
            head.append("")
        parts = head
        for source, cs in sections:
            parts.append(f"### {source} ({len(cs)})")
            parts.extend(render_change(source, k, o, v) for k, o, v in cs[:40])
            if len(cs) > 40:
                parts.append(f"…and {len(cs) - 40} more; see the commit diff.")
        if blind:
            parts.append(f"### Gone blind ({len(blind)})")
            parts.append(f"These have failed {config.BLIND_AFTER} runs in a row:")
            parts.extend(f"- `{k}`: {e}" for k, e in blind)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(parts) + "\n")
        title_bits = [s for s, _ in sections] + (["blind"] if blind else [])
        title = f"Label watch: {n} change(s) [{', '.join(title_bits)}]"
        with open(os.path.join(OUT, "title.txt"), "w") as f:
            f.write(title)
    elif os.path.exists(report_path):
        os.remove(report_path)

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")

    print(f"{now}: sources={sorted(results)} errors={len(errors)} "
          f"changed={changed}")
    for k, e in errors.items():
        print(f"  error {k}: {e}", file=sys.stderr)
    return changed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    run(only=set(filter(None, a.only.split(","))))
