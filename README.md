# Google-Accountability (label-watch)

Watches how Google labels the Gulf of Mexico / "Gulf of America" and Lake Ontario / "Lake America", and opens a GitHub issue when anything changes. Runs on GitHub Actions about every 15 minutes. No server.

## What it checks

| Source | How | What a change means |
|---|---|---|
| `maps` | Headless Chrome loads Google Maps for each name as seen from the US, Canada, Mexico and the UK (the `gl=` parameter), reads the place name and panel text, and OCRs the screenshot for the label drawn on the map | Google changed the name, panel text or map label for that country's view. A tooltip or provenance note would most likely surface here |
| `places` | Places API (New) text search per region (us, ca, mx, gb). Optional, needs a key | Google's structured name and `editorialSummary` changed for a given country |
| `earth` | Headless Chrome loads Google Earth's search for each place and reads whatever text it can reach | Best effort. Earth draws labels on the globe as pixels, so only its side-card text gets compared. Screenshots go in the run artifacts |
| `pages` | Fingerprints Google's two rename blog posts and the Maps help page on borders and names | Google quietly edited its own explanation |
| `feeds` | Google's blog RSS and a Google News search for the names, items from the last two weeks | A new post or story. Catches a future rename too |
| `fedreg` | Federal Register API for the names and the Board on Geographic Names | Upstream action. Google follows GNIS, so this often comes first |

The first run of each source records a baseline and stays quiet. After that, a text change gets reported with a diff, and a new feed item or document gets reported as new. When wording such as "renamed", "executive order" or "also known as" first appears in a panel, it gets flagged at the top of the issue.

If a source fails four runs in a row, the bot opens a "gone blind" issue, so silence means nothing changed rather than that the bot broke.

Every snapshot gets committed to `snapshots/`, so the git history works as a dated record of what Google showed and when.

## Setup

1. Push this folder to a GitHub repo with Actions enabled. In a public repo, Actions minutes are free and the snapshot history is a public record.
2. Optional, `GOOGLE_MAPS_API_KEY` secret: a Google Cloud key with Places API (New) enabled. This is the only source that sees the Canadian, Mexican and rest-of-world views. The web probes run from US runners. It makes 16 calls a run. At a run every 15 minutes, that comes to about 46,000 a month, well past any free allowance, and requesting `editorialSummary` puts those calls in a higher pricing tier. If you add a key, set a budget alert first, or call the API only on some runs.
3. Optional, `NTFY_TOPIC` secret: a hard-to-guess topic name. Install the ntfy app and subscribe to that topic for a phone push on each alert. Without it, GitHub's own issue email and mobile notifications do the job.
4. Run it once by hand (Actions → label-watch → Run workflow) to set the baseline.

## Keeping it running

Three independent layers start checks, so one failing doesn't stop them:

1. GitHub's scheduler, at :07, :22, :37 and :52 past each hour. GitHub skips scheduled slots under load, and sometimes for days on new repos.
2. A chain: each run starts the next one 15 minutes after it began (the `next` job). Concurrency collapses extra starts into one queue, so runs never pile up.
3. An hourly outside watchdog, a Claude scheduled task, that starts a run if none has started in the last 40 minutes and raises a flag if the last few runs failed.

To stop all of it, disable the workflow under Actions.

## Status page

Every run rebuilds a public page from the snapshots and deploys it to GitHub Pages: rossgoodwin.com/google-accountability. It shows what Google Maps calls each place in each country, what Google Earth labels it, this run's screenshots, the days since Google was asked to add a provenance note (from `NOTICE_DATE` in `watch/config.py`), and every change the bot has reported. It requires **Settings → Pages → Source: GitHub Actions** to be set once. The page builder lives in `watch/site.py`.

The table on the page shows only names Google puts in the page itself. OCR readings of the map stay out of it, since OCR can catch part of a label and miss the rest. They still trigger alerts.

## What it saw at launch (2026-11-26)

- US: "Gulf of America", "Lake America", on Maps and on Earth
- Canada: "Lake Ontario", "Gulf of Mexico (Gulf of America)"
- Mexico: "Gulf of Mexico", "Lake Ontario (Lake America)"
- UK: both names in each case

The full baseline lives in `snapshots/`.

## Tuning

If the first few days flag noise from the Maps panel (counts, opening hours, rotating chrome), add a regex to `NOISE_PATTERNS` in `watch/config.py`. Names, regions, feeds and watched pages also live in that file.

Maps sometimes serves automated visitors a cut-down "limited view" panel. Those panels are skipped rather than compared, so the name and the map label get checked every run, and the longer panel text only on runs where Google serves it in full.

To see what the probes actually read, run the workflow by hand with **debug** ticked. Raw OCR text, Earth's page text and small Earth screenshots land in `snapshots/_debug/`. Delete that folder afterwards.

Run the tests with `python -m pytest`.
