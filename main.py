import os
import time
import json
import hashlib
import requests
import feedparser

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CHECK_INTERVAL = 300
STATE_FILE = "seen_alerts.json"

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=iran+OR+israel+OR+war+OR+oil+OR+nato+OR+protest+OR+lockdown+OR+evacuation+OR+airspace&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=flight+ban+OR+travel+warning+OR+closed+airspace+OR+state+of+emergency&hl=en-US&gl=US&ceid=US:en",
]

CATEGORIES = {
    "KRIEG": [
        "war", "conflict", "attack", "missile", "rocket", "drone strike",
        "airstrike", "invasion", "troops", "military", "nato deployment"
    ],
    "ÖL": [
        "oil", "oil price", "oil spike", "brent", "crude", "strait of hormuz",
        "hormuz", "energy crisis", "gas prices"
    ],
    "FLUGSPERRE": [
        "closed airspace", "airspace closed", "flight ban", "flights suspended",
        "airport closed", "aviation warning", "travel disruption"
    ],
    "EVAKUIERUNG": [
        "evacuation", "evacuate", "embassy evacuation", "citizens warned",
        "ordered to leave", "emergency evacuation"
    ],
    "PROTESTE": [
        "protest", "demonstration", "riot", "unrest", "clashes", "mass protests",
        "state of emergency", "lockdown", "curfew"
    ]
}

HIGH_RISK_WORDS = [
    "missile", "airstrike", "invasion", "martial law", "state of emergency",
    "closed airspace", "embassy evacuation", "strait of hormuz", "oil spike",
    "rocket", "drone strike", "flights suspended"
]

def send_telegram(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM Variablen fehlen.")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    r = requests.post(url, data=data, timeout=15)
    print("Telegram status:", r.status_code, r.text)

def load_seen():
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception as e:
        print("Fehler beim Laden:", e)
        return set()

def save_seen(seen):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Fehler beim Speichern:", e)

def make_id(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def detect_category(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    for category, keywords in CATEGORIES.items():
        for kw in keywords:
            if kw in text:
                return category
    return "SONSTIGES"

def classify_risk(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    for kw in HIGH_RISK_WORDS:
        if kw in text:
            return "HIGH"

    if detect_category(title, summary) != "SONSTIGES":
        return "MEDIUM"

    return "LOW"

def should_alert(category: str, risk: str) -> bool:
    if risk == "HIGH":
        return True
    if risk == "MEDIUM" and category != "SONSTIGES":
        return True
    return False

def format_message(category: str, risk: str, title: str, link: str, source: str) -> str:
    if risk == "HIGH":
        icon = "🔴"
    else:
        icon = "🟠"

    return (
        f"{icon} {category}-ALERT [{risk}]\n\n"
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

            category = detect_category(title, summary)
            risk = classify_risk(title, summary)

            if should_alert(category, risk):
                msg = format_message(category, risk, title, link, source)
                send_telegram(msg)
                seen.add(alert_id)
                new_alerts += 1

    save_seen(seen)
    print("Neue Alerts:", new_alerts)

def run_cycle():
    print("Version 3 Check läuft...")
    check_feeds()
    time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    send_telegram("✅ Krisen-Agent V3 gestartet")
    while True:
        try:
            run_cycle()
        except Exception as e:
            print("Fehler:", e)
            time.sleep(30)