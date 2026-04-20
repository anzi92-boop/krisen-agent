import os
import time
import json
import hashlib
import requests
import feedparser
from datetime import datetime

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CHECK_INTERVAL = 300
STATE_FILE = "agent_state.json"

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=iran+OR+israel+OR+war+OR+oil+OR+nato+OR+protest+OR+lockdown+OR+evacuation+OR+airspace+OR+hormuz&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=flight+ban+OR+travel+warning+OR+closed+airspace+OR+state+of+emergency+OR+missile+attack&hl=en-US&gl=US&ceid=US:en",
]

WATCH_REGIONS = [
    "iran", "israel", "gaza", "lebanon", "syria", "iraq",
    "yemen", "saudi", "qatar", "uae", "emirates", "oman",
    "hormuz", "russia", "ukraine", "taiwan", "china",
    "usa", "united states", "nato", "europe", "germany",
    "france", "britain", "uk", "austria"
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
    "rocket", "drone strike", "flights suspended", "mass evacuation"
]

SUMMARY_HOUR_UTC = 18


def send_telegram(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM Variablen fehlen.")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    r = requests.post(url, data=data, timeout=15)
    print("Telegram status:", r.status_code, r.text)


def load_state():
    default_state = {
        "seen_ids": [],
        "recent_titles": [],
        "daily_counts": {},
        "last_summary_date": ""
    }

    if not os.path.exists(STATE_FILE):
        return default_state

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for key, value in default_state.items():
                if key not in data:
                    data[key] = value
            return data
    except Exception as e:
        print("Fehler beim Laden vom State:", e)
        return default_state


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Fehler beim Speichern vom State:", e)


def make_id(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def normalize_title(title: str) -> str:
    title = title.lower().strip()
    for ch in [",", ".", ":", ";", "-", "–", "—", "!", "?", "(", ")", "[", "]", '"', "'"]:
        title = title.replace(ch, " ")
    title = " ".join(title.split())
    return title


def is_relevant_region(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    return any(region in text for region in WATCH_REGIONS)


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

    category = detect_category(title, summary)
    if category != "SONSTIGES":
        return "MEDIUM"

    return "LOW"


def should_alert(category: str, risk: str, title: str, summary: str) -> bool:
    if not is_relevant_region(title, summary):
        return False

    if risk == "HIGH":
        return True

    if risk == "MEDIUM" and category != "SONSTIGES":
        return True

    return False


def is_duplicate_title(normalized_title: str, recent_titles: list) -> bool:
    return normalized_title in recent_titles


def remember_title(state, normalized_title: str, max_titles: int = 200):
    state["recent_titles"].append(normalized_title)
    if len(state["recent_titles"]) > max_titles:
        state["recent_titles"] = state["recent_titles"][-max_titles:]


def remember_seen_id(state, alert_id: str, max_ids: int = 1000):
    state["seen_ids"].append(alert_id)
    if len(state["seen_ids"]) > max_ids:
        state["seen_ids"] = state["seen_ids"][-max_ids:]


def increment_daily_count(state, category: str):
    if category not in state["daily_counts"]:
        state["daily_counts"][category] = 0
    state["daily_counts"][category] += 1


def format_message(category: str, risk: str, title: str, link: str, source: str) -> str:
    icon = "🔴" if risk == "HIGH" else "🟠"
    return (
        f"{icon} {category}-ALERT [{risk}]\n\n"
        f"{title}\n\n"
        f"Quelle: {source}\n"
        f"{link}"
    )


def maybe_send_daily_summary(state):
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")

    if now.hour < SUMMARY_HOUR_UTC:
        return

    if state["last_summary_date"] == today:
        return

    total = sum(state["daily_counts"].values())

    if total == 0:
        summary = "📊 TAGESZUSAMMENFASSUNG\n\nHeute wurden keine relevanten Krisen-Alerts erkannt."
    else:
        lines = [f"📊 TAGESZUSAMMENFASSUNG", "", f"Gesamt: {total} Alerts", ""]
        for category, count in sorted(state["daily_counts"].items(), key=lambda x: x[0]):
            lines.append(f"- {category}: {count}")
        summary = "\n".join(lines)

    send_telegram(summary)
    state["last_summary_date"] = today
    state["daily_counts"] = {}


def check_feeds():
    state = load_state()
    new_alerts = 0

    seen_ids = set(state["seen_ids"])
    recent_titles = state["recent_titles"]

    for feed_url in RSS_FEEDS:
        print("Prüfe Feed:", feed_url)
        feed = feedparser.parse(feed_url)

        for entry in feed.entries[:25]:
            title = getattr(entry, "title", "").strip()
            summary = getattr(entry, "summary", "").strip()
            link = getattr(entry, "link", "").strip()
            source = getattr(entry, "source", {}).get("title", "Unbekannt") if hasattr(entry, "source") else "Unbekannt"

            if not title:
                continue

            normalized_title = normalize_title(title)
            alert_id = make_id(title + link)

            if alert_id in seen_ids:
                continue

            if is_duplicate_title(normalized_title, recent_titles):
                continue

            category = detect_category(title, summary)
            risk = classify_risk(title, summary)

            if should_alert(category, risk, title, summary):
                msg = format_message(category, risk, title, link, source)
                send_telegram(msg)

                remember_seen_id(state, alert_id)
                remember_title(state, normalized_title)
                increment_daily_count(state, category)

                seen_ids.add(alert_id)
                recent_titles = state["recent_titles"]
                new_alerts += 1

    maybe_send_daily_summary(state)
    save_state(state)
    print("Neue Alerts:", new_alerts)


def run_cycle():
    print("Version 4 Check läuft...")
    check_feeds()
    time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    send_telegram("✅ Krisen-Agent V4 gestartet")
    while True:
        try:
            run_cycle()
        except Exception as e:
            print("Fehler:", e)
            time.sleep(30)