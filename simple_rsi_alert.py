"""
Basit RSI14 Eşik Uyarı Botu (kademeli botla İLGİSİZ, bağımsız çalışır)
==============================================================================
Sadece 5 DAKİKALIK grafikte RSI14 hesaplar. Değer 25 VE ALTINA inerse
doğrudan Telegram'a bildirim gönderir. Ara aşama (15dk/30dk) kontrolü YOKTUR
- bu, ana kademeli botun basitleştirilmiş, bağımsız bir versiyonudur.

GitHub Actions'ta her 5 dakikada bir tek seferlik çalışır.
Aynı Telegram hesabına (aynı BOT_TOKEN/CHAT_ID) mesaj gönderir, ama kendi
ayrı state dosyasını (simple_alert_state.json) kullanır - ana botun
cooldown'undan tamamen bağımsızdır.
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone

BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
RSI_PERIOD = 14
RSI_THRESHOLD = 25

SYMBOLS = ["BTCUSDT", "PAXGUSDT"]

SIGNAL_COOLDOWN_MINUTES = 60  # aynı sembol için tekrar bildirim göndermeden önce bekleme
STATE_FILE = "simple_alert_state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("simple_rsi_alert")


def get_closes(symbol, interval="5m", limit=200):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return [float(k[4]) for k in data]


def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        raise ValueError("RSI hesaplamak için yeterli veri yok")
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID tanımlı değil.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=15)
        r.raise_for_status()
        log.info(f"Telegram mesajı gönderildi ({r.json().get('ok')}).")
    except Exception as e:
        log.error(f"Telegram mesajı gönderilemedi: {e}")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def cooldown_active(state, symbol):
    last = state.get(symbol)
    if not last:
        return False
    last_dt = datetime.fromisoformat(last)
    elapsed_min = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
    return elapsed_min < SIGNAL_COOLDOWN_MINUTES


def check_symbol(symbol, state):
    closes = get_closes(symbol, "5m")
    rsi = calculate_rsi(closes, RSI_PERIOD)
    log.info(f"{symbol} 5dk RSI14: {rsi}")

    if rsi > RSI_THRESHOLD:
        return

    if cooldown_active(state, symbol):
        log.info(f"{symbol} RSI eşiği geçildi ama cooldown aktif, tekrar bildirim gönderilmiyor.")
        return

    message = (
        f"⚠️ <b>{symbol} RSI14 UYARISI</b>\n\n"
        f"5dk RSI14: {rsi} (eşik: {RSI_THRESHOLD} ve altı)\n\n"
        f"Zaman: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    send_telegram_message(message)
    state[symbol] = datetime.now(timezone.utc).isoformat()


def main():
    log.info("Basit RSI14 uyarı kontrolü başladı.")
    state = load_state()
    for symbol in SYMBOLS:
        try:
            check_symbol(symbol, state)
        except Exception as e:
            log.error(f"{symbol} kontrol edilirken hata: {e}")
    save_state(state)
    log.info("Kontrol tamamlandı.")


if __name__ == "__main__":
    main()
