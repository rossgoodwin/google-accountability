"""GitHub issues for possible changes. Uses the workflow's token; without one
(tests, local runs) every call is a no-op that returns None."""
import os

import requests

API = "https://api.github.com"
LABELS = {
    "label-watch": "B60205",
    "unconfirmed": "FBCA04",
    "confirmed": "0E8A16",
    "reverted": "BFD4F2",
}


def _repo_and_headers():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return None, None
    return repo, {"Authorization": f"Bearer {token}",
                  "Accept": "application/vnd.github+json"}


def _call(method, path, **kw):
    repo, h = _repo_and_headers()
    if not repo:
        return None
    r = requests.request(method, f"{API}/repos/{repo}{path}", headers=h, timeout=30, **kw)
    if r.status_code >= 400 and not (method == "POST" and path == "/labels"):
        r.raise_for_status()
    return r.json() if r.content else {}


def ensure_labels():
    for name, color in LABELS.items():
        try:
            _call("POST", "/labels", json={"name": name, "color": color})
        except Exception:
            pass


def open_issue(title, body, labels):
    res = _call("POST", "/issues", json={"title": title, "body": body, "labels": labels})
    return res.get("number") if res else None


def comment(number, body):
    if number:
        _call("POST", f"/issues/{number}/comments", json={"body": body})


def relabel(number, labels):
    if number:
        _call("PUT", f"/issues/{number}/labels", json={"labels": labels})


def close(number, reason="completed"):
    if number:
        _call("PATCH", f"/issues/{number}", json={"state": "closed", "state_reason": reason})
