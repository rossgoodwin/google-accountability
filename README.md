# Google-Accountability (label-watch)

Watches how Google labels the Gulf of Mexico / "Gulf of America" and Lake Ontario / "Lake America", and opens a GitHub issue when anything changes. Runs on GitHub Actions every six hours. No server.

## What it checks

| Source | How | What a change means |
|---|---|---|
| `maps` | Headless Chrome loads Google Maps for each name, reads the place panel's title and text, saves a screenshot | Google changed the name, description or panel text a US visitor sees. A tooltip or provenance note would most likely surface here |
| `places` | Places API (New) text search per region (us, ca, mx, gb). Optional, needs a key | Google's structured name and `editorialSummary` changed for a given country |
| `earth` | Headless Chrome loads Google Earth's search for each place and reads whatever text it can reach | Best effort. Earth draws labels on the globe as pixels, so only its side-card text gets compared. Screenshots go in the run artifacts |
| `pages` | Fingerprints Google's two rename blog posts and the Maps help page on borders and names | Google quietly edited its own explanation |
| `feeds` | Google's blog RSS and a Google News search for the names | A new post or story. Catches a future rename too |
| `fedreg` | Federal Register API for the names and the Board on Geographic Names | Upstream action. Google follows GNIS, so this often comes first |

The first run of each source records a baseline and stays quiet. After that, a text change gets reported with a diff, and a new feed item or document gets reported as new. When wording such as "renamed", "executive order" or "also known as" first appears in a panel, it gets flagged at the top of the issue.

If a source fails four runs in a row, the bot opens a "gone blind" issue, so silence means nothing changed rather than that the bot broke.

Every snapshot gets committed to `snapshots/`, so the git history works as a dated record of what Google showed and when.

## Setup

1. Create a private repo, push this folder to it, and enable Actions.
2. Optional, `GOOGLE_MAPS_API_KEY` secret: a Google Cloud key with Places API (New) enabled. This is the only source that sees the Canadian, Mexican and rest-of-world views. The web probes run from US runners. It makes 16 calls a run, about 1,900 a month. Requesting `editorialSummary` puts those calls in a higher pricing tier, so check Google's current free monthly cap for that tier, set a budget alert, or drop the cron to daily.
3. Optional, `NTFY_TOPIC` secret: a hard-to-guess topic name. Install the ntfy app and subscribe to that topic for a phone push on each alert. Without it, GitHub's own issue email and mobile notifications do the job.
4. Run it once by hand (Actions → label-watch → Run workflow) to set the baseline.

## Tuning

If the first few days flag noise from the Maps panel (counts, opening hours, rotating chrome), add a regex to `NOISE_PATTERNS` in `watch/config.py`. Names, regions, feeds and watched pages also live in that file.

Run the tests with `python -m pytest`.
