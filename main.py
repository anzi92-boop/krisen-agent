import feedparser

KEYWORDS = [
    "war", "conflict", "iran", "israel",
    "oil", "nato", "attack",
    "protest", "lockdown", "emergency"
]

def run_cycle():
    print("News Check läuft...")

    feed = feedparser.parse("https://news.google.com/rss")

    for entry in feed.entries[:10]:
        title = entry.title.lower()

        for keyword in KEYWORDS:
            if keyword in title:
                send_telegram(f"🚨 KRISEN ALERT:\n{entry.title}")
                break

    time.sleep(300)