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
    "https://news.google.com/rss/search?q=iran+OR+israel+OR+war+OR+oil+OR+nato+OR+protest+OR+lockdown+OR+evacuation+OR+airspace+OR+hormuz+OR+travel+warning&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=flight+ban+OR+travel+warning+OR+closed+airspace+OR+state+of+emergency+OR+missile+attack+OR+embassy+evacuation&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=brent+oil+OR+wti+oil+OR+crude+oil+price+OR+strait+of+hormuz&hl=en-US&gl=US&ceid=US:en",
]

WATCH_REGIONS = [
    "iran", "israel", "gaza", "lebanon", "syria", "iraq",
    "yemen", "saudi", "qatar", "uae", "emirates", "oman",
    "hormuz", "strait of hormuz", "red sea",
    "russia", "ukraine", "taiwan", "china",
    "usa", "united states", "nato", "europe", "germany",
    "france", "britain", "uk", "austria"
]

CATEGORIES = {
    "KRIEG": [
        "war", "conflict", "attack", "missile", "rocket", "drone strike",
        "airstrike", "invasion", "troops", "military", "nato deployment",
        "military operation", "border clash"
    ],
    "ÖL": [
        "oil", "oil price", "oil spike", "brent", "wti", "crude", "crude oil",
        "strait of hormuz", "hormuz", "energy crisis", "gas prices",
        "oil market", "supply disruption", "shipping disruption"
    ],
    "FLUGSPERRE": [
        "closed airspace", "airspace closed", "flight ban", "flights suspended",
        "airport closed", "aviation warning", "travel disruption",
        "air traffic suspended", "airspace restriction"
    ],
    "EVAKUIERUNG": [
        "evacuation", "evacuate", "embassy evacuation", "citizens warned",
        "ordered to leave", "emergency evacuation", "leave immediately"
    ],
    "PROTESTE": [
        "protest", "demonstration", "riot", "unrest", "clashes", "mass protests",
        "state of emergency", "lockdown", "curfew", "civil unrest"
    ],
    "REISEWARNUNG": [
        "travel warning", "travel advisory", "do not travel",
        "avoid all travel", "security alert", "tourist warning"
    ]
}

HIGH_RISK_WORDS = [
    "missile", "airstrike", "invasion", "martial law", "state of emergency",
    "closed airspace", "embassy evacuation", "strait of hormuz", "oil spike",
    "rocket", "drone strike", "flights suspended", "mass evacuation",
    "do not travel", "avoid all travel", "airport closed", "leave immediately"
]

BLACKLIST_WORDS = [
    "football", "soccer", "celebrity", "movie", "music", "tv show",
    "stock picks", "shopping", "weather forecast", "recipe", "gaming",
    "transfer rumor", "entertainment", "fashion"
]

SUMMARY_HOUR_UTC = 18
MEDIUM_DIGEST_MIN_ITEMS = 3


def send_telegram(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM Variablen fehlen.")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    r = requests.post(url, data=data, timeout=20)
    print("Telegram status:", r.status_code, r.text)


def load_state():
    default_state = {
        "seen_ids": [],
        "recent_titles": [],
        "daily_counts": {},
        "last_summary_date": "",
        "medium_digest_queue": [],
        "last_medium_digest_sent": ""
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
    return " ".join(title.split())


def text_blob(title: str, summary: str) -> str:
    return f"{title} {summary}".lower()


def is_blacklisted(title: str, summary: str) -> bool:
    text = text_blob(title, summary)
    return any(word in text for word in BLACKLIST_WORDS)


def is_relevant_region(title: str, summary: str) -> bool:
    text = text_blob(title, summary)
    return any(region in text for region in WATCH_REGIONS)


def detect_category(title: str, summary: str) -> str:
    text = text_blob(title, summary)
    for category, keywords in CATEGORIES.items():
        for kw in keywords:
            if kw in text:
                return category
    return "SONSTIGES"


def classify_risk(title: str, summary: str, category: str) -> str:
    text = text_blob(title, summary)

    for kw in HIGH_RISK_WORDS:
        if kw in text:
            return "HIGH"

    if category in ["FLUGSPERRE", "EVAKUIERUNG", "REISEWARNUNG"]:
        return "HIGH"

    if category == "ÖL":
        if any(x in text for x in ["oil spike", "supply disruption", "strait of hormuz", "shipping disruption"]):
            return "HIGH"
        return "MEDIUM"

    if category in ["KRIEG", "PROTESTE"]:
        return "MEDIUM"

    return "LOW"


def should_alert(category: str, risk: str, title: str, summary: str) -> bool:
    if is_blacklisted(title, summary):
        return False

    text = text_blob(title, summary)

    if category == "ÖL":
        return any(x in text for x in [
            "brent", "wti", "oil", "crude", "hormuz", "supply disruption", "shipping disruption"
        ])

    if not is_relevant_region(title, summary) and category not in ["ÖL"]:
        return False

    return risk in ["HIGH", "MEDIUM"]


def is_duplicate_title(normalized_title: str, recent_titles: list) -> bool:
    return normalized_title in recent_titles


def remember_title(state, normalized_title: str, max_titles: int = 300):
    state["recent_titles"].append(normalized_title)
    if len(state["recent_titles"]) > max_titles:
        state["recent_titles"] = state["recent_titles"][-max_titles:]


def remember_seen_id(state, alert_id: str, max_ids: int = 1500):
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


def queue_medium_alert(state, category: str, title: str, link: str, source: str):
    item = {
        "category": category,
        "title": title,
        "link": link,
        "source": source
    }
    state["medium_digest_queue"].append(item)

    if len(state["medium_digest_queue"]) > 50:
        state["medium_digest_queue"] = state["medium_digest_queue"][-50:]


def maybe_send_medium_digest(state):
    today_hour = datetime.utcnow().strftime("%Y-%m-%d-%H")
    queue = state["medium_digest_queue"]

    if len(queue) < MEDIUM_DIGEST_MIN_ITEMS:
        return

    if state["last_medium_digest_sent"] == today_hour:
        return

    lines = ["🟠 MEDIUM-SAMMELALERT", ""]
    for item in queue[:10]:
        lines.append(f"- [{item['category']}] {item['title']}")
        lines.append(f"  Quelle: {item['source']}")
        lines.append(f"  {item['link']}")
        lines.append("")

    send_telegram("\n".join(lines).strip())
    state["medium_digest_queue"] = []
    state["last_medium_digest_sent"] = today_hour


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


def process_entry(state, entry):
    title = getattr(entry, "title", "").strip()
    summary = getattr(entry, "summary", "").strip()
    link = getattr(entry, "link", "").strip()
    source = getattr(entry, "source", {}).get("title", "Unbekannt") if hasattr(entry, "source") else "Unbekannt"

    if not title:
        return False

    normalized_title = normalize_title(title)
    alert_id = make_id(title + link)

    if alert_id in set(state["seen_ids"]):
        return False

    if is_duplicate_title(normalized_title, state["recent_titles"]):
        return False

    category = detect_category(title, summary)
    risk = classify_risk(title, summary, category)

    if not should_alert(category, risk, title, summary):
        return False

    remember_seen_id(state, alert_id)
    remember_title(state, normalized_title)
    increment_daily_count(state, category)

    if risk == "HIGH":
        msg = format_message(category, risk, title, link, source)
        send_telegram(msg)
    else:
        queue_medium_alert(state, category, title, link, source)

    return True


def check_feeds():
    state = load_state()
    new_alerts = 0

    for feed_url in RSS_FEEDS:
        print("Prüfe Feed:", feed_url)
        feed = feedparser.parse(feed_url)

        for entry in feed.entries[:25]:
            try:
                if process_entry(state, entry):
                    new_alerts += 1
            except Exception as e:
                print("Fehler bei Entry:", e)

    maybe_send_medium_digest(state)
    maybe_send_daily_summary(state)
    save_state(state)
    print("Neue Alerts:", new_alerts)


def run_cycle():
    print("Version 5 Check läuft...")
    check_feeds()
    time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    send_telegram("✅ Krisen-Agent V5 gestartet")
    while True:
        try:
            run_cycle()
        except Exception as e:
            print("Fehler im Hauptloop:", e)
            time.sleep(30)