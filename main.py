import os
import time
import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM Variablen fehlen.")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    r = requests.post(url, data=data, timeout=15)
    print("Telegram status:", r.status_code, r.text)

def run_cycle():
    print("Check läuft...")
    time.sleep(60)

if __name__ == "__main__":
    send_telegram("✅ Krisen-Agent auf Railway gestartet")
    while True:
        try:
            run_cycle()
        except Exception as e:
            print("Fehler:", e)
            time.sleep(30)