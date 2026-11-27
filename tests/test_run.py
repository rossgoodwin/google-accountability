import json
import os

import pytest

from watch import probes, run


def test_normalize_drops_churn():
    raw = "Gulf of America\n4.5\n(12,345)\n12,345 reviews\nDirections\nBody of water\n  Body of water "
    assert probes.normalize(raw) == "Gulf of America\nBody of water"


def test_compare_baseline_is_silent():
    assert run.compare("text", None, {"a": "x"}) == []
    assert run.compare("set", None, {"a": "x"}) == []


def test_compare_text_and_set():
    assert run.compare("text", {"a": "x", "b": "y"}, {"a": "x2", "c": "z"}) == [("a", "x", "x2")]
    assert run.compare("set", {"a": "x"}, {"a": "x", "b": "y"}) == [("b", None, "y")]


def test_merge_keeps_missing_text_keys():
    assert run.merge("text", {"a": "1", "b": "2"}, {"a": "3"}) == {"a": "3", "b": "2"}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    state = {"name": "Lake America", "panel": "Lake America\nLake", "feed": {}, "fail": False}

    def browser(_):
        if state["fail"]:
            raise RuntimeError("blocked")
        recs = {"lake|Lake Ontario|gl=us|name": state["name"],
                "lake|Lake Ontario|gl=us|on_screen": state.get("ocr", "Lake America"),
                "lake|Lake Ontario|gl=us|panel": probes.normalize(state["panel"])}
        return {"kind": "text", "records": recs}, {"kind": "text", "records": {}}, {}

    monkeypatch.setattr(probes, "browser_probes", browser)
    monkeypatch.setattr(probes, "places_api", lambda: None)
    monkeypatch.setattr(probes, "pages", lambda: {"kind": "text", "records": {"p": "same"}})
    monkeypatch.setattr(probes, "feeds", lambda: {"kind": "set", "records": dict(state["feed"])})
    monkeypatch.setattr(probes, "fedreg", lambda: {"kind": "set", "records": {}})
    return state


def test_end_to_end(fake):
    assert run.run() is False                      # baseline
    assert run.run() is False                      # nothing new
    fake["panel"] = "Lake America\nLake\nRenamed by executive order in 2026; also known as Lake Ontario"
    assert run.run() is True
    report = open("out/report.md").read()
    assert "Possible provenance language appeared" in report
    assert "executive order" in report
    assert "maps" in open("out/title.txt").read()
    assert run.run() is False and not os.path.exists("out/report.md")

    fake["feed"] = {"google_news|https://x": "Google adds note to Lake America"}
    assert run.run() is True
    assert "Google adds note" in open("out/report.md").read()


def test_goes_blind_once(fake):
    run.run()
    fake["fail"] = True
    results = [run.run() for _ in range(6)]
    assert results == [False, False, False, True, False, False]
    assert json.load(open("snapshots/_health.json"))["browser"] == 6
    fake["fail"] = False
    run.run()
    assert json.load(open("snapshots/_health.json")) == {}


def test_ocr_needs_two_runs(fake):
    run.run()
    fake["ocr"] = "none"          # one-off misread
    assert run.run() is False
    fake["ocr"] = "Lake America"  # back to normal: nothing pending
    assert run.run() is False
    assert json.load(open("snapshots/_pending.json")) == {}
    fake["ocr"] = "Lake Ontario (Lake America)"   # real change
    assert [run.run() for _ in range(3)] == [False, False, False]   # held
    assert run.run() is True                       # confirmed on the 4th check
    assert "Lake Ontario (Lake America)" in open("out/report.md").read()
    assert run.run() is False


def test_meters_and_thanks(fake, monkeypatch):
    from watch import meters
    state = {"name": "Lake America"}
    real_views = meters.views
    monkeypatch.setattr(meters, "views", lambda m, e: {
        "restored|maps|lake": (meters._score_names(state["name"], "lake"), state["name"]),
        "restored|earth|lake": (0.0, ""), "restored|maps|gulf": (0.0, ""),
        "restored|earth|gulf": (0.0, ""), "explained|lake": (0.0, ""), "explained|gulf": (0.0, "")})
    run.run()
    assert json.load(open("snapshots/_meters.json"))["restored"] == 0
    assert run.run() is False
    state["name"] = "Lake America (Lake Ontario)"
    assert run.run() is True
    m = json.load(open("snapshots/_meters.json"))
    assert m["restored"] == 13 and len(m["thanks"]) == 1 and "Thank you." in m["thanks"][0]
    assert open("out/title.txt").read().startswith("Names restored 13%")
    assert "0% \u2192 13%" in open("out/report.md").read()
    assert run.run() is False


def test_two_misreads_do_not_count(fake):
    run.run()
    fake["ocr"] = "Gulf of America, misread"
    assert [run.run() for _ in range(2)] == [False, False]
    fake["ocr"] = "Lake America"                   # back to the real reading
    assert run.run() is False
    assert json.load(open("snapshots/_pending.json")) == {}
    import os
    assert not os.path.exists("snapshots/_events.json")   # nothing reached the page log
