import os
import time
import json
import hashlib
import requests
import feedparser

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CHECK_INTERVAL = 300  # 5 Minuten
STATE_FILE = "seen_alerts.json"

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=iran+OR+israel+OR+war+OR+oil+OR+nato+OR+protest+OR+lockdown&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=middle+east+conflict+OR+missile+attack+OR+state+of+emergency&hl=en-US&gl=US&ceid=US:en",
]

KEYWORDS_HIGH = [
    "missile", "airstrike", "attack", "invasion", "mobilization",
    "evacuation", "state of emergency", "martial law", "closed airspace",
    "strait of hormuz", "oil spike", "nato deployment", "rocket", "drone strike"
]

KEYWORDS_MEDIUM = [
    "iran", "israel", "war", "conflict", "military", "troops",
    "protest", "lockdown", "travel warning", "sanctions", "oil", "gas prices"
]

def send_telegram(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM Variablen fehlen.")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": msg
    }

    r = requests.post(url, data=data, timeout=15)
    print("Telegram status:", r.status_code, r.text)

def load_seen():
    if not os.path.exists(STATE_FILE):
        return set()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data)
    except Exception as e:
        print("Fehler beim Laden von seen_alerts:", e)
        return set()

def save_seen(seen):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Fehler beim Speichern von seen_alerts:", e)

def make_id(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def classify_risk(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()

    for kw in KEYWORDS_HIGH:
        if kw in text:
            return "HIGH"

    for kw in KEYWORDS_MEDIUM:
        if kw in text:
            return "MEDIUM"

    return "LOW"

def should_alert(risk: str) -> bool:
    return risk in ["HIGH", "MEDIUM"]

def format_message(risk: str, title: str, link: str, source: str) -> str:
    icon = "🔴" if risk == "HIGH" else "🟠"
    return (
        f"{icon} KRISEN-ALERT [{risk}]\n\n"
        f"{title}\n\n"
        f"Quelle: {source}\n"
        f"{link}"
    )

def check_feeds():
    seen = load_seen()
    new_alerts = 0

    for feed_url in RSS_FEEDS:
        print("Prüfe Feed:", feed_url)
        feed = feedparser.parse(feed_url)

        for entry in feed.entries[:20]:
            title = getattr(entry, "title", "").strip()
            summary = getattr(entry, "summary", "").strip()
            link = getattr(entry, "link", "").strip()
            source = getattr(entry, "source", {}).get("title", "Unbekannt") if hasattr(entry, "source") else "Unbekannt"

            if not title:
                continue

            alert_id = make_id(title + link)

            if alert_id in seen:
                continue

            risk = classify_risk(title, summary)

            if should_alert(risk):
                msg = format_message(risk, title, link, source)
                send_telegram(msg)
                seen.add(alert_id)
                new_alerts += 1

    save_seen(seen)
    print(f"Neue Alerts: {new_alerts}")

def run_cycle():
    print("News-Check läuft...")
    check_feeds()
    time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    send_telegram("✅ Krisen-Agent V2 gestartet")
    while True:
        try:
            run_cycle()
        except Exception as e:
            print("Fehler im Hauptloop:", e)
            time.sleep(30)