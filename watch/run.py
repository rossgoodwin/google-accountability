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

from . import config, issues, meters, probes

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


def show(v):
    return "no label read" if v in (None, "none") else f"\u201c{v}\u201d"


def open_possible(source, k, o, v, now):
    from .site import pretty_key
    issues.ensure_labels()
    what = pretty_key(source, k)
    title = f"Possible change: {what}: {show(o)} \u2192 {show(v)}"
    run_url = os.environ.get("RUN_URL", "")
    body = (f"At {now} the bot read {show(v)} where it last confirmed {show(o)}.\n\n"
            "This reading comes from a screenshot, read by OCR, so it may be a misread. "
            f"If the old reading comes back, this issue closes itself. If the new one holds "
            f"for {config.OCR_CONFIRM_RUNS} checks in a row (about an hour), it is confirmed "
            "and the status page updates."
            + (f"\n\nRun: {run_url}" if run_url else ""))
    try:
        return issues.open_issue(title, body, ["label-watch", "unconfirmed"])
    except Exception as e:
        print(f"  could not open issue: {e!r}", file=sys.stderr)
        return None


def log_events(source, changes, now):
    """Event log for the status page: every confirmed change, newest last."""
    events = load(os.path.join(SNAP, "_events.json"), [])
    for k, o, v in changes:
        events.append({
            "time": now, "source": source, "key": k,
            "old": None if o is None else o[:500], "new": (v or "")[:500],
            "provenance": bool(o is not None and
                               set(provenance_hits(v)) - set(provenance_hits(o))),
        })
    save(os.path.join(SNAP, "_events.json"), events[-500:])


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

    # GOOG price for the status page. Not compared or alerted on; a failed
    # fetch keeps the last price, which the page shows with its own time.
    if want("stock"):
        try:
            path = os.path.join(SNAP, "_stock.json")
            save(path, meters.merge_stock(load(path, None), probes.stock(now)))
        except Exception as e:
            errors["stock"] = repr(e)[:300]

    # Compare and store. Readings taken off screenshots ("|on_screen") can
    # misread, so a departure from the last confirmed reading opens a
    # "Possible change" issue right away, closes it if the old reading comes
    # back, and confirms it if the new one holds for OCR_CONFIRM_RUNS checks.
    # Only confirmed readings reach the snapshots, meters and status page.
    watching = load(os.path.join(SNAP, "_open.json"), {})
    confirmed_issues = []
    sections = []
    for source, r in results.items():
        path = os.path.join(SNAP, f"{source}.json")
        old = load(path, None)
        records = dict(r["records"])
        changes = []
        for k, o, v in compare(r["kind"], old, records):
            if not k.endswith("|on_screen"):
                changes.append((k, o, v))
                continue
            pk = f"{source}|{k}"
            if v == "none" and o in (None, "none"):
                continue
            records[k] = o              # hold the confirmed reading for now
            w = watching.get(pk)
            if w and w["new"] == v:
                w["count"] += 1
            elif w:
                issues.comment(w["issue"], f"Check at {now} now reads {show(v)}.")
                w.update(new=v, count=1)
            else:
                w = watching[pk] = {"old": o, "new": v, "count": 1, "since": now,
                                    "issue": open_possible(source, k, o, v, now)}
            if w["count"] >= config.OCR_CONFIRM_RUNS:
                issues.comment(w["issue"], f"Confirmed: {show(v)} held for "
                               f"{w['count']} checks in a row, since {w['since']}. "
                               "The status page now shows it.")
                issues.relabel(w["issue"], ["label-watch", "confirmed"])
                confirmed_issues.append(w["issue"])
                watching.pop(pk)
                records[k] = v
                changes.append((k, o, v))
        # A reading that came back to the confirmed value closes its issue.
        for pk in [p for p in watching if p.startswith(source + "|")]:
            k = pk[len(source) + 1:]
            if k in r["records"] and old and r["records"][k] == old.get(k):
                w = watching.pop(pk)
                issues.comment(w["issue"], f"Back to {show(old.get(k))} at {now}, after "
                               f"{w['count']} check(s). Most likely a misread, so this "
                               "is closed and the status page never changed.")
                issues.relabel(w["issue"], ["label-watch", "reverted"])
                issues.close(w["issue"], "not_planned")
        save(path, merge(r["kind"], old, records))
        # Screenshot changes have their own issues; keep them out of the
        # end-of-run report but in the page log and meters.
        report_changes = [c for c in changes if not c[0].endswith("|on_screen")]
        if changes:
            log_events(source, changes, now)
        if report_changes:
            sections.append((source, report_changes))
    save(os.path.join(SNAP, "_open.json"), watching)

    # Meters for the top of the status page, and a dated thank-you line for
    # every view that improved.
    mstate = load(os.path.join(SNAP, "_meters.json"), None)
    new_views = meters.views(load(os.path.join(SNAP, "maps.json"), {}),
                             load(os.path.join(SNAP, "earth.json"), {}))
    restored, explained = meters.totals(new_views)
    meter_note = ""
    if mstate is None:
        mstate = {"history": [], "thanks": []}
    else:
        old_views = {k: tuple(v) for k, v in mstate.get("views", {}).items()}
        new_thanks = meters.thanks_lines(old_views, new_views, now[:10])
        mstate["thanks"] = mstate.get("thanks", []) + new_thanks
        before = (mstate.get("restored", 0), mstate.get("explained", 0))
        if (restored, explained) != before:
            meter_note = (f"Names restored for US users: {before[0]}% → {restored}%. "
                          f"Origin explained: {before[1]}% → {explained}%.")
            mstate["history"].append({"time": now, "restored": restored,
                                      "explained": explained})
        if new_thanks:
            meter_note += "\n\n" + "\n".join(new_thanks)
    mstate.update({"views": {k: list(v) for k, v in new_views.items()},
                   "restored": restored, "explained": explained})
    save(os.path.join(SNAP, "_meters.json"), mstate)



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
    save(os.path.join(SNAP, "_last_errors.json"), errors)   # for diagnosis

    # Report.
    os.makedirs(OUT, exist_ok=True)
    report_path = os.path.join(OUT, "report.md")
    # A meter moved only by a confirmed screenshot change goes on that
    # change's own issue instead of opening another.
    if meter_note and not sections and not blind and confirmed_issues:
        for n in confirmed_issues:
            issues.comment(n, meter_note.strip())
        meter_note = ""
    changed = bool(sections or blind or meter_note)
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
        if meter_note:
            parts.append("### Meters\n\n" + meter_note.strip())
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
        title = f"Change detected: {n} [{', '.join(title_bits)}]"
        if meter_note:
            title = f"Names restored {restored}%, origin explained {explained}%: {title}"
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
