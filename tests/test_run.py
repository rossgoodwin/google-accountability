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
    monkeypatch.setattr(probes, "stock", lambda now: {"symbol": "GOOG", "price": 1.0,
                                                      "market_time": 0, "daily_closes": {}})
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


@pytest.fixture
def gh(monkeypatch):
    from watch import issues
    calls = []
    n = iter(range(100, 200))
    monkeypatch.setattr(issues, "ensure_labels", lambda: None)
    monkeypatch.setattr(issues, "open_issue", lambda t, b, l: calls.append(("open", t, l)) or next(n))
    monkeypatch.setattr(issues, "comment", lambda num, b: calls.append(("comment", num, b)))
    monkeypatch.setattr(issues, "relabel", lambda num, l: calls.append(("relabel", num, l)))
    monkeypatch.setattr(issues, "close", lambda num, reason="completed": calls.append(("close", num, reason)))
    return calls


def test_misread_opens_then_closes_itself(fake, gh):
    run.run()
    fake["ocr"] = "none"                       # label vanishes from the screenshot
    run.run()
    assert gh[0][0] == "open" and gh[0][1].startswith("Possible change:")
    assert "no label read" in gh[0][1] and gh[0][2] == ["label-watch", "unconfirmed"]
    run.run()                                  # still blank: same issue, no new one
    assert sum(c[0] == "open" for c in gh) == 1
    fake["ocr"] = "Lake America"               # back: closes itself
    run.run()
    assert ("close", 100, "not_planned") in gh
    assert ("relabel", 100, ["label-watch", "reverted"]) in gh
    assert json.load(open("snapshots/_open.json")) == {}
    import os
    assert not os.path.exists("snapshots/_events.json")   # page never changed
    m = json.load(open("snapshots/maps.json"))
    assert m["lake|Lake Ontario|gl=us|on_screen"] == "Lake America"


def test_real_change_confirms_after_an_hour(fake, gh):
    run.run()
    fake["ocr"] = "Lake Ontario (Lake America)"
    for _ in range(3):
        run.run()
    assert json.load(open("snapshots/maps.json"))["lake|Lake Ontario|gl=us|on_screen"] == "Lake America"
    run.run()                                   # 4th check in a row: confirmed
    assert ("relabel", 100, ["label-watch", "confirmed"]) in gh
    assert not any(c[0] == "close" for c in gh)
    assert json.load(open("snapshots/maps.json"))["lake|Lake Ontario|gl=us|on_screen"] == "Lake Ontario (Lake America)"
    ev = json.load(open("snapshots/_events.json"))
    assert ev[-1]["new"] == "Lake Ontario (Lake America)"


def test_blank_view_that_was_always_blank_stays_quiet(fake, gh):
    fake["ocr"] = "none"
    run.run()
    run.run()
    assert gh == []


def test_parse_stock_and_day_move():
    from watch import probes, site
    data = {"chart": {"result": [{"meta": {"symbol": "GOOG", "regularMarketPrice": 101.0,
            "regularMarketTime": 1764252000, "gmtoffset": -18000},
            "timestamp": [1764079200, 1764165600, 1764252000],
            "indicators": {"quote": [{"close": [100.0, None, 101.0]}]}}]}}
    st = probes.parse_stock(data, "now")
    assert st["price"] == 101.0 and len(st["daily_closes"]) == 2
    days = sorted(st["daily_closes"])
    close, pct = site.day_move(st["daily_closes"], days[1])
    assert close == 101.0 and round(pct, 2) == 1.0
    assert site.day_move(st["daily_closes"], days[0]) is None


def test_merge_stock_builds_history():
    from watch import meters
    a = meters.merge_stock(None, {"price": 100.0, "market_time": 1764277200, "previous_close": 99.0})
    b = meters.merge_stock(a, {"price": 101.0, "market_time": 1764277200 + 86400})
    assert list(b["daily_closes"].values()) == [100.0, 101.0]


def test_looks_like_map(tmp_path):
    from PIL import Image, ImageDraw
    from watch.probes import looks_like_map
    globe = tmp_path / "globe.png"
    im = Image.new("RGB", (800, 500), (40, 80, 140))       # water
    ImageDraw.Draw(im).rectangle((0, 0, 800, 200), fill=(70, 100, 50))   # land
    im.save(globe)
    assert looks_like_map(str(globe), "Lake America Camera: 800 km", "earth")[0]
    dash = tmp_path / "dash.png"
    Image.new("RGB", (800, 500), (250, 250, 250)).save(dash)
    assert not looks_like_map(str(dash), "", "earth")[0]
    assert not looks_like_map(str(globe), "Map projects No projects yet", "earth")[0]
    roadmap = tmp_path / "road.png"
    im = Image.new("RGB", (800, 500), (248, 249, 250))     # Maps land
    ImageDraw.Draw(im).rectangle((200, 100, 700, 400), fill=(170, 218, 255))   # Maps water
    im.save(roadmap)
    assert looks_like_map(str(roadmap), "", "maps")[0]
    assert not looks_like_map(str(dash), "Before you continue to Google", "maps")[0]
    assert not looks_like_map(str(dash), "", "maps")[0]


def test_nasdaq_uses_regular_close_after_hours(monkeypatch):
    from watch import probes
    class R:
        def raise_for_status(self): pass
        def json(self):
            return {"data": {"marketStatus": "After-Hours",
                "primaryData": {"lastSalePrice": "$274.03", "netChange": "+1.10",
                                "lastTradeTimestamp": "Nov 27, 2026 3:45 PM ET"},
                "secondaryData": {"lastSalePrice": "$273.10", "netChange": "+0.95",
                                  "lastTradeTimestamp": "Closed at Nov 27, 2026 1:00 PM ET"}}}
    monkeypatch.setattr(probes.requests, "get", lambda *a, **k: R())
    q = probes._nasdaq("now")
    assert q["price"] == 273.10 and q["session"] == "close" and q["previous_close"] == 272.15
