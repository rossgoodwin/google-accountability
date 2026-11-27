"""What the bot watches. Edit freely; the next run re-baselines anything new."""

# The two bodies of water, each with the names Google might show for it.
PLACES = {
    "gulf": {
        "queries": ["Gulf of America", "Gulf of Mexico"],
        "names": ["Gulf of America", "Gulf of Mexico", "Golfo de México", "Golfo de América"],
        # Google Earth camera: lat, lon, and the distances (metres) to look from.
        "earth": (25.5, -90.0, [2600000, 1300000]),
    },
    "lake": {
        "queries": ["Lake America", "Lake Ontario"],
        "names": ["Lake America", "Lake Ontario", "Lac Ontario"],
        "earth": (43.65, -77.85, [1400000, 800000, 450000]),
    },
}

# Region views to ask for. The web probes run from a US GitHub runner, so the
# gl= parameter is a best-effort hint; the Places API honours regionCode exactly.
REGIONS = ["us", "ca", "mx", "gb"]

# Words that mark a provenance note or tooltip, the change Ross asked for.
# Any of these appearing in a place panel is flagged at the top of the alert.
PROVENANCE_WORDS = [
    "executive order", "renamed", "formerly", "also known as", "GNIS",
    "Geographic Names", "official name", "name change", "disputed",
    "known as", "previously", "provenance",
]

# Official Google text to fingerprint. A change to any of these is reported
# with a diff.
PAGES = {
    "blog_gulf_2025": "https://blog.google/products/maps/united-states-geographic-name-change-feb-2025/",
    "blog_lake_2026": "https://blog.google/products-and-platforms/products/maps/gnis-lake-ontario-lake-america-name-change/",
    "help_borders_names": "https://support.google.com/maps/answer/3145721?hl=en",
}

# Feeds scanned for new items mentioning any keyword. Only new items alert.
FEEDS = {
    "google_blog": "https://blog.google/rss/",
    "google_maps_blog": "https://blog.google/products/maps/rss/",
    "google_news": (
        "https://news.google.com/rss/search?q=%22Lake+America%22+OR+%22Gulf+of+America%22"
        "+%22Google+Maps%22+OR+%22Google+Earth%22&hl=en-US&gl=US&ceid=US:en"
    ),
}
FEED_KEYWORDS = [
    "Lake America", "Gulf of America", "Lake Ontario", "Gulf of Mexico",
    "GNIS", "Geographic Names", "name change", "rename",
]

# Upstream: Google follows GNIS, and GNIS follows federal action. New Federal
# Register documents on these terms often come before a Maps change.
FEDREG_TERMS = ["Lake America", "Gulf of America", "Board on Geographic Names"]

# Panel lines that churn on their own (ratings, review counts, photo counts,
# "Open now" style chrome). Lines matching any of these are dropped before
# comparing. Add patterns here if a run flags noise.
NOISE_PATTERNS = [
    r"^\(?[\d.,]+\)?$",                 # bare numbers, ratings
    r"[\d,.]+[KkMm]?\s+reviews?",       # review counts
    r"[\d,.]+[KkMm]?\s+photos?",        # photo counts
    r"^(Directions|Save|Nearby|Send to phone|Share|Photos|Reviews|Overview|About)$",
    r"^(Suggest an edit|Add a missing place|Add your business|Add a label)$",
    r"^\d+ (min|hr|h)\b",
    r"^Sponsored",
    r"^Update results when map moves$",
    r"^See more$",
]

# Labels read off screenshots by OCR can misread for a check or two. A new
# reading has to come back this many checks in a row (about an hour) before
# it counts as a change, reaches the meters or shows on the page.
OCR_CONFIRM_RUNS = 4

# How many failing runs in a row before a source counts as "blind" and alerts.
BLIND_AFTER = 4

# Status page (GitHub Pages). The date Google was asked to add a provenance
# note to the renamed water bodies; the page counts days from it.
NOTICE_DATE = "2026-11-26"
REPO_URL = "https://github.com/rossgoodwin/google-accountability"
