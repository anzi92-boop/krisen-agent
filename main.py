import os
import time
import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    requests.post(url, data=data, timeout=10)

def run_cycle():
    print("Test läuft...")
    send_telegram("🚨 TEST ALERT – System funktioniert!")
    time.sleep(300)

if __name__ == "__main__":
    send_telegram("✅ Test gestartet")

    while True:
        run_cycle()