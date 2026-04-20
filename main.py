import os
import re
import time
import json
import hashlib
import requests
import feedparser
from datetime import datetime
from html import unescape

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CHECK_INTERVAL = 30
HTTP_TIMEOUT = 20
STATE_FILE = "agent_state.json"

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=iran+OR+israel+OR+war+OR+oil+OR+nato+OR+protest+OR+lockdown+OR+evacuation+OR+airspace+OR+hormuz+OR+travel+warning&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=flight+ban+OR+travel+warning+OR+closed+airspace+OR+state+of+emergency+OR+missile+attack+OR+embassy+evacuation&hl=en-US&gl=US&ceid=US:en",
]

USGS_SIGNIFICANT_EARTHQUAKES_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_hour.geojson"
USGS_ALL_M45_EARTHQUAKES_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_hour.geojson"

# Offizielle Direktquellen
EIA_DAILY_PRICES_URL = "https://www.eia.gov/todayinenergy/prices.php"
STATE_TRAVEL_ADVISORIES_URL = "https://cadataapi.state.gov/api/TravelAdvisories"

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
        "avoid all travel", "security alert", "tourist warning",
        "reconsider travel"
    ],
    "ERDBEBEN": [
        "earthquake", "quake", "seismic", "aftershock"
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
HIGH_ALERT_COOLDOWN_MINUTES = 20
MEDIUM_DIGEST_COOLDOWN_MINUTES = 30

# Öl-Logik
BRENT_HIGH_PRICE = 100.0
WTI_HIGH_PRICE = 90.0
PRICE_SPIKE_PERCENT = 3.0


def utc_now():
    return datetime.utcnow()


def now_iso():
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def send_telegram(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM Variablen fehlen.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": msg,
        "disable_web_page_preview": True
    }

    try:
        r = requests.post(url, data=data, timeout=HTTP_TIMEOUT)
        print("Telegram status:", r.status_code, r.text[:300])
        return r.status_code == 200
    except Exception as e:
        print("Telegram Fehler:", e)
        return False


def load_state():
    default_state = {
        "seen_ids": [],
        "recent_titles": [],
        "daily_counts": {},
        "last_summary_date": "",
        "medium_digest_queue": [],
        "last_medium_digest_sent_at": "",
        "high_alert_history": {},
        "oil_last": {
            "brent_price": None,
            "brent_change_pct": None,
            "wti_price": None,
            "wti_change_pct": None,
            "last_check": ""
        }
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

    if category in ["KRIEG", "PROTESTE", "ERDBEBEN"]:
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

    if category == "ERDBEBEN":
        return True

    if not is_relevant_region(title, summary) and category != "REISEWARNUNG":
        return False

    return risk in ["HIGH", "MEDIUM"]


def is_duplicate_title(normalized_title: str, recent_titles: list) -> bool:
    return normalized_title in recent_titles


def remember_title(state, normalized_title: str, max_titles: int = 500):
    state["recent_titles"].append(normalized_title)
    if len(state["recent_titles"]) > max_titles:
        state["recent_titles"] = state["recent_titles"][-max_titles:]


def remember_seen_id(state, alert_id: str, max_ids: int = 3000):
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


def format_earthquake_message(magnitude: float, place: str, link: str, source: str, ts_ms: int) -> str:
    risk = "HIGH" if magnitude >= 6.0 else "MEDIUM"
    icon = "🔴" if risk == "HIGH" else "🟠"
    dt = datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"{icon} ERDBEBEN-ALERT [{risk}]\n\n"
        f"Magnitude: {magnitude}\n"
        f"Ort: {place}\n"
        f"Zeit: {dt}\n\n"
        f"Quelle: {source}\n"
        f"{link}"
    )


def minutes_since(iso_string: str):
    if not iso_string:
        return None
    try:
        past = datetime.strptime(iso_string, "%Y-%m-%dT%H:%M:%SZ")
        diff = utc_now() - past
        return diff.total_seconds() / 60
    except Exception:
        return None


def high_alert_on_cooldown(state, cooldown_key: str) -> bool:
    history = state.get("high_alert_history", {})
    last_sent = history.get(cooldown_key, "")
    mins = minutes_since(last_sent)
    return mins is not None and mins < HIGH_ALERT_COOLDOWN_MINUTES


def mark_high_alert_sent(state, cooldown_key: str):
    state["high_alert_history"][cooldown_key] = now_iso()
    if len(state["high_alert_history"]) > 1500:
        items = list(state["high_alert_history"].items())[-1500:]
        state["high_alert_history"] = dict(items)


def queue_medium_alert(state, category: str, title: str, link: str, source: str):
    item = {
        "category": category,
        "title": title,
        "link": link,
        "source": source
    }
    state["medium_digest_queue"].append(item)

    if len(state["medium_digest_queue"]) > 100:
        state["medium_digest_queue"] = state["medium_digest_queue"][-100:]


def maybe_send_medium_digest(state):
    queue = state["medium_digest_queue"]
    if len(queue) < MEDIUM_DIGEST_MIN_ITEMS:
        return

    mins = minutes_since(state.get("last_medium_digest_sent_at", ""))
    if mins is not None and mins < MEDIUM_DIGEST_COOLDOWN_MINUTES:
        return

    lines = ["🟠 MEDIUM-SAMMELALERT", ""]
    for item in queue[:10]:
        lines.append(f"- [{item['category']}] {item['title']}")
        lines.append(f"  Quelle: {item['source']}")
        lines.append(f"  {item['link']}")
        lines.append("")

    if send_telegram("\n".join(lines).strip()):
        state["medium_digest_queue"] = []
        state["last_medium_digest_sent_at"] = now_iso()


def maybe_send_daily_summary(state):
    now = utc_now()
    today = now.strftime("%Y-%m-%d")

    if now.hour < SUMMARY_HOUR_UTC:
        return

    if state["last_summary_date"] == today:
        return

    total = sum(state["daily_counts"].values())

    if total == 0:
        summary = "📊 TAGESZUSAMMENFASSUNG\n\nHeute wurden keine relevanten Krisen-Alerts erkannt."
    else:
        lines = ["📊 TAGESZUSAMMENFASSUNG", "", f"Gesamt: {total} Alerts", ""]
        for category, count in sorted(state["daily_counts"].items(), key=lambda x: x[0]):
            lines.append(f"- {category}: {count}")
        summary = "\n".join(lines)

    if send_telegram(summary):
        state["last_summary_date"] = today
        state["daily_counts"] = {}


def process_rss_entry(state, entry):
    title = getattr(entry, "title", "").strip()
    summary = getattr(entry, "summary", "").strip()
    link = getattr(entry, "link", "").strip()
    source = getattr(entry, "source", {}).get("title", "Unbekannt") if hasattr(entry, "source") else "Unbekannt"

    if not title:
        return False

    normalized_title = normalize_title(title)
    alert_id = make_id(title + link)

    if alert_id in state["seen_ids"]:
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
        cooldown_key = f"{category}:{normalized_title[:120]}"
        if high_alert_on_cooldown(state, cooldown_key):
            print("HIGH auf Cooldown:", cooldown_key)
            return False

        msg = format_message(category, risk, title, link, source)
        if send_telegram(msg):
            mark_high_alert_sent(state, cooldown_key)
    else:
        queue_medium_alert(state, category, title, link, source)

    return True


def process_usgs_feed(state, url):
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print("USGS Fehler:", e)
        return 0

    alerts = 0
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        quake_id = feature.get("id", "")
        mag = props.get("mag")
        place = props.get("place", "Unbekannt")
        link = props.get("url", "")
        ts_ms = props.get("time", 0)

        if quake_id is None or mag is None:
            continue

        alert_id = f"usgs:{quake_id}"
        if alert_id in state["seen_ids"]:
            continue

        remember_seen_id(state, alert_id)
        increment_daily_count(state, "ERDBEBEN")

        risk = "HIGH" if float(mag) >= 6.0 else "MEDIUM"
        source = "USGS"

        if risk == "HIGH":
            cooldown_key = f"ERDBEBEN:{quake_id}"
            if not high_alert_on_cooldown(state, cooldown_key):
                msg = format_earthquake_message(float(mag), place, link, source, ts_ms)
                if send_telegram(msg):
                    mark_high_alert_sent(state, cooldown_key)
        else:
            title = f"M {mag} earthquake - {place}"
            queue_medium_alert(state, "ERDBEBEN", title, link, source)

        alerts += 1

    return alerts


def extract_price_and_change(html: str, label: str):
    # Beispiel-Zeile: Brent 116.63 +1.5
    pattern = rf"{label}\s+([0-9]+(?:\.[0-9]+)?)\s+([+-]?[0-9]+(?:\.[0-9]+)?)"
    match = re.search(pattern, html, re.IGNORECASE)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def check_eia_oil_prices(state):
    try:
        r = requests.get(EIA_DAILY_PRICES_URL, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        html = unescape(r.text)
    except Exception as e:
        print("EIA Fehler:", e)
        return 0

    brent_price, brent_change = extract_price_and_change(html, "Brent")
    wti_price, wti_change = extract_price_and_change(html, "WTI")

    if brent_price is None and wti_price is None:
        print("Keine Ölpreise gefunden.")
        return 0

    alerts = 0
    last = state.get("oil_last", {})

    messages = []

    if brent_price is not None:
        is_high = brent_price >= BRENT_HIGH_PRICE or (brent_change is not None and brent_change >= PRICE_SPIKE_PERCENT)
        if is_high:
            messages.append(
                f"🔴 ÖL-ALERT [HIGH]\n\nBrent: ${brent_price:.2f}/bbl\nÄnderung: {brent_change:+.1f}%\n\nQuelle: EIA\n{EIA_DAILY_PRICES_URL}"
            )

    if wti_price is not None:
        is_high = wti_price >= WTI_HIGH_PRICE or (wti_change is not None and wti_change >= PRICE_SPIKE_PERCENT)
        if is_high:
            messages.append(
                f"🔴 ÖL-ALERT [HIGH]\n\nWTI: ${wti_price:.2f}/bbl\nÄnderung: {wti_change:+.1f}%\n\nQuelle: EIA\n{EIA_DAILY_PRICES_URL}"
            )

    for msg in messages:
        cooldown_key = make_id(msg[:120])
        if not high_alert_on_cooldown(state, cooldown_key):
            if send_telegram(msg):
                mark_high_alert_sent(state, cooldown_key)
                increment_daily_count(state, "ÖL")
                alerts += 1

    state["oil_last"] = {
        "brent_price": brent_price,
        "brent_change_pct": brent_change,
        "wti_price": wti_price,
        "wti_change_pct": wti_change,
        "last_check": now_iso()
    }

    return alerts


def travel_level_to_risk(level_text: str):
    text = (level_text or "").lower()

    if "level 4" in text or "do not travel" in text:
        return "HIGH", "REISEWARNUNG"
    if "level 3" in text or "reconsider travel" in text:
        return "MEDIUM", "REISEWARNUNG"
    return None, None


def check_state_travel_advisories(state):
    try:
        r = requests.get(STATE_TRAVEL_ADVISORIES_URL, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print("State Travel API Fehler:", e)
        return 0

    alerts = 0

    items = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
    for item in items:
        country = (
            item.get("country")
            or item.get("CountryName")
            or item.get("name")
            or item.get("title")
            or "Unbekannt"
        )
        level = (
            item.get("advisoryLevel")
            or item.get("TravelAdvisory")
            or item.get("level")
            or item.get("Level")
            or ""
        )
        link = (
            item.get("url")
            or item.get("Url")
            or item.get("link")
            or "https://travel.state.gov/content/travel/en/traveladvisories/traveladvisories.html"
        )
        updated = item.get("lastUpdated") or item.get("LastUpdateDate") or ""

        risk, category = travel_level_to_risk(str(level))
        if not risk:
            continue

        title = f"{country} Travel Advisory - {level}".strip()
        alert_id = make_id(f"state-travel:{country}:{level}:{updated}")

        if alert_id in state["seen_ids"]:
            continue

        remember_seen_id(state, alert_id)
        increment_daily_count(state, category)

        source = "U.S. Department of State"
        if risk == "HIGH":
            cooldown_key = f"TRAVEL:{country}:{level}"
            if high_alert_on_cooldown(state, cooldown_key):
                continue

            msg = format_message(category, risk, title, link, source)
            if send_telegram(msg):
                mark_high_alert_sent(state, cooldown_key)
                alerts += 1
        else:
            queue_medium_alert(state, category, title, link, source)
            alerts += 1

    return alerts


def check_rss_feeds(state):
    new_alerts = 0

    for feed_url in RSS_FEEDS:
        print("Prüfe RSS:", feed_url)
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print("RSS Fehler:", e)
            continue

        for entry in feed.entries[:30]:
            try:
                if process_rss_entry(state, entry):
                    new_alerts += 1
            except Exception as e:
                print("Fehler bei RSS Entry:", e)

    return new_alerts


def check_direct_sources(state):
    count = 0
    count += process_usgs_feed(state, USGS_SIGNIFICANT_EARTHQUAKES_URL)
    count += process_usgs_feed(state, USGS_ALL_M45_EARTHQUAKES_URL)
    count += check_eia_oil_prices(state)
    count += check_state_travel_advisories(state)
    return count


def run_cycle():
    print("Version 8 Hybrid Direct Monitoring läuft...")

    state = load_state()

    rss_alerts = check_rss_feeds(state)
    direct_alerts = check_direct_sources(state)

    maybe_send_medium_digest(state)
    maybe_send_daily_summary(state)
    save_state(state)

    total = rss_alerts + direct_alerts
    print("Neue Alerts gesamt:", total)
    time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    send_telegram("✅ Krisen-Agent V8 Hybrid Direct Monitoring gestartet")
    while True:
        try:
            run_cycle()
        except Exception as e:
            print("Fehler im Hauptloop:", e)
            time.sleep(10)