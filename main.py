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

# Optional für später:
ICAO_API_KEY = os.getenv("ICAO_API_KEY")

CHECK_INTERVAL = 30
HTTP_TIMEOUT = 20
STATE_FILE = "agent_state.json"

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=iran+OR+israel+OR+war+OR+oil+OR+nato+OR+protest+OR+lockdown+OR+evacuation+OR+airspace+OR+hormuz+OR+travel+warning&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=flight+ban+OR+travel+warning+OR+closed+airspace+OR+state+of+emergency+OR+missile+attack+OR+embassy+evacuation&hl=en-US&gl=US&ceid=US:en",
]

USGS_SIGNIFICANT_EARTHQUAKES_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_hour.geojson"
USGS_ALL_M45_EARTHQUAKES_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_hour.geojson"
EIA_DAILY_PRICES_URL = "https://www.eia.gov/todayinenergy/prices.php"

# Offizielle State Department Seiten
STATE_TRAVEL_ADVISORIES_PAGE = "https://travel.state.gov/en/international-travel/travel-advisories.html"
STATE_MIDDLE_EAST_PAGE = "https://travel.state.gov/en/international-travel/travel-advisories/global-events/middle-east.html"

WATCH_REGIONS = [
    "iran", "israel", "gaza", "west bank", "lebanon", "syria", "iraq",
    "yemen", "saudi", "qatar", "uae", "emirates", "oman", "jordan",
    "bahrain", "kuwait", "egypt",
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
        "air traffic suspended", "airspace restriction", "notam"
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
        "reconsider travel", "exercise increased caution"
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
        print("Telegram status:", r.status_code, r.text[:200])
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


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(unescape(text).split())


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

    if category in ["ERDBEBEN", "REISEWARNUNG", "FLUGSPERRE"]:
        return True

    if not is_relevant_region(title, summary):
        return False

    return risk in ["HIGH", "MEDIUM"]


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
    summary = strip_html(getattr(entry, "summary", "").strip())
    link = getattr(entry, "link", "").strip()
    source = getattr(entry, "source", {}).get("title", "Unbekannt") if hasattr(entry, "source") else "Unbekannt"

    if not title:
        return False

    normalized_title = normalize_title(title)
    alert_id = make_id(title + link)

    if alert_id in state["seen_ids"]:
        return False
    if normalized_title in state["recent_titles"]:
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
            return False
        if send_telegram(format_message(category, risk, title, link, source)):
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
        if risk == "HIGH":
            cooldown_key = f"ERDBEBEN:{quake_id}"
            if not high_alert_on_cooldown(state, cooldown_key):
                if send_telegram(format_earthquake_message(float(mag), place, link, "USGS", ts_ms)):
                    mark_high_alert_sent(state, cooldown_key)
        else:
            queue_medium_alert(state, "ERDBEBEN", f"M {mag} earthquake - {place}", link, "USGS")
        alerts += 1

    return alerts


def extract_price_and_change(html: str, label: str):
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
        return 0

    alerts = 0
    if brent_price is not None:
        is_high = brent_price >= BRENT_HIGH_PRICE or (brent_change is not None and brent_change >= PRICE_SPIKE_PERCENT)
        if is_high:
            msg = f"🔴 ÖL-ALERT [HIGH]\n\nBrent: ${brent_price:.2f}/bbl\nÄnderung: {brent_change:+.1f}%\n\nQuelle: EIA\n{EIA_DAILY_PRICES_URL}"
            key = f"OIL:BRENT:{int(brent_price)}:{int(brent_change or 0)}"
            if not high_alert_on_cooldown(state, key):
                if send_telegram(msg):
                    mark_high_alert_sent(state, key)
                    increment_daily_count(state, "ÖL")
                    alerts += 1

    if wti_price is not None:
        is_high = wti_price >= WTI_HIGH_PRICE or (wti_change is not None and wti_change >= PRICE_SPIKE_PERCENT)
        if is_high:
            msg = f"🔴 ÖL-ALERT [HIGH]\n\nWTI: ${wti_price:.2f}/bbl\nÄnderung: {wti_change:+.1f}%\n\nQuelle: EIA\n{EIA_DAILY_PRICES_URL}"
            key = f"OIL:WTI:{int(wti_price)}:{int(wti_change or 0)}"
            if not high_alert_on_cooldown(state, key):
                if send_telegram(msg):
                    mark_high_alert_sent(state, key)
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


def check_state_middle_east_page(state):
    try:
        r = requests.get(STATE_MIDDLE_EAST_PAGE, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        html = unescape(r.text)
    except Exception as e:
        print("State Middle East Fehler:", e)
        return 0

    alerts = 0

    patterns = [
        ("WORLDWIDE CAUTION", r"exercise increased caution"),
        ("MIDDLE EAST SECURITY", r"latest security updates"),
        ("IRAN", r"### Iran"),
        ("IRAQ", r"### Iraq"),
        ("ISRAEL/WEST BANK/GAZA", r"### Israel, West Bank, and Gaza"),
        ("YEMEN", r"### Yemen"),
    ]

    for label, patt in patterns:
        if re.search(patt, html, re.IGNORECASE):
            alert_id = make_id(f"state-middle-east:{label}:2026")
            if alert_id in state["seen_ids"]:
                continue

            remember_seen_id(state, alert_id)
            increment_daily_count(state, "REISEWARNUNG")

            title = f"{label} security/travel update on official State Department Middle East page"
            queue_medium_alert(state, "REISEWARNUNG", title, STATE_MIDDLE_EAST_PAGE, "U.S. Department of State")
            alerts += 1

    return alerts


def check_state_specific_advisories_from_page(state):
    try:
        r = requests.get(STATE_TRAVEL_ADVISORIES_PAGE, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        html = unescape(r.text)
    except Exception as e:
        print("State Advisories Fehler:", e)
        return 0

    alerts = 0

    triggers = [
        ("WORLDWIDE CAUTION", "exercise increased caution", "MEDIUM"),
        ("DO NOT TRAVEL", "Do not travel", "HIGH"),
        ("RECONSIDER TRAVEL", "Reconsider your travel", "MEDIUM"),
    ]

    for label, needle, risk in triggers:
        if needle.lower() not in html.lower():
            continue

        alert_id = make_id(f"travel-page:{label}")
        if alert_id in state["seen_ids"]:
            continue

        remember_seen_id(state, alert_id)
        increment_daily_count(state, "REISEWARNUNG")

        title = f"Official travel advisory page contains: {label}"
        if risk == "HIGH":
            key = f"STATE_TRAVEL:{label}"
            if not high_alert_on_cooldown(state, key):
                if send_telegram(format_message("REISEWARNUNG", "HIGH", title, STATE_TRAVEL_ADVISORIES_PAGE, "U.S. Department of State")):
                    mark_high_alert_sent(state, key)
                    alerts += 1
        else:
            queue_medium_alert(state, "REISEWARNUNG", title, STATE_TRAVEL_ADVISORIES_PAGE, "U.S. Department of State")
            alerts += 1

    return alerts


def check_aviation_placeholder(state):
    # Solange kein ICAO_API_KEY gesetzt ist, nur Hinweis im Log.
    if not ICAO_API_KEY:
        print("Aviation API Key fehlt - ICAO/FAA Direktmodul noch nicht aktiv.")
        return 0

    # Platz für später: echter ICAO API Call mit Key
    return 0


def check_rss_feeds(state):
    count = 0
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print("RSS Fehler:", e)
            continue

        for entry in feed.entries[:30]:
            try:
                if process_rss_entry(state, entry):
                    count += 1
            except Exception as e:
                print("Fehler bei RSS Entry:", e)
    return count


def check_direct_sources(state):
    count = 0
    count += process_usgs_feed(state, USGS_SIGNIFICANT_EARTHQUAKES_URL)
    count += process_usgs_feed(state, USGS_ALL_M45_EARTHQUAKES_URL)
    count += check_eia_oil_prices(state)
    count += check_state_middle_east_page(state)
    count += check_state_specific_advisories_from_page(state)
    count += check_aviation_placeholder(state)
    return count


def run_cycle():
    print("Version 9 Official Hybrid Monitoring läuft...")
    state = load_state()

    rss_alerts = check_rss_feeds(state)
    direct_alerts = check_direct_sources(state)

    maybe_send_medium_digest(state)
    maybe_send_daily_summary(state)
    save_state(state)

    print("Neue Alerts gesamt:", rss_alerts + direct_alerts)
    time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    send_telegram("✅ Krisen-Agent V9 Official Hybrid Monitoring gestartet")
    while True:
        try:
            run_cycle()
        except Exception as e:
            print("Fehler im Hauptloop:", e)
            time.sleep(10)