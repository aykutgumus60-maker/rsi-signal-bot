"""
BTC & PAXG - 5dk -> 15dk -> 30dk Kademeli RSI14 AL Sinyali Botu
=================================================================
GitHub Actions üzerinde her 5 dakikada bir TEK SEFERLİK çalışacak
şekilde tasarlanmıştır (sonsuz döngü YOKTUR - Actions'ta buna
gerek yok, zamanlamayı workflow dosyası yapıyor).

Mantık (her sembol için ayrı ayrı):
  1) 5dk grafikte RSI14 <= RSI_5M_THRESHOLD ise
  2) 15dk grafikte SON 15M_WINDOW_MINUTES dakika içinde
     RSI14 herhangi bir mumda <= RSI_15M_THRESHOLD olduysa
  3) 30dk grafikte SON 30M_WINDOW_MINUTES dakika içinde
     RSI14 herhangi bir mumda <= RSI_30M_THRESHOLD olduysa
     -> Telegram'a AL sinyali gönderilir.

Aynı sinyalin tekrar tekrar gönderilmemesi için basit bir
cooldown durumu 'state.json' dosyasında saklanır ve workflow
tarafından repoya geri commit'lenir.
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone

# ============ AYARLAR ============
SYMBOLS = ["BTCUSDT", "PAXGUSDT"]

RSI_PERIOD = 14

RSI_5M_THRESHOLD = 30
RSI_15M_THRESHOLD = 35
RSI_30M_THRESHOLD = 35

# "son X dakika içinde eşiğin altına inmiş mi" pencereleri
WINDOW_15M_MINUTES = 20
WINDOW_30M_MINUTES = 40

SIGNAL_COOLDOWN_MINUTES = 60  # aynı sembol için tekrar sinyal göndermeden önce bekleme

BINANCE_KLINES_URL = BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
STATE_FILE = "state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("rsi_bot")


def get_klines(symbol: str, interval: str, limit: int = 200):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    closes = [float(k[4]) for k in data]
    return closes


def calculate_rsi_series(closes, period: int = 14):
    """
    Wilder RSI14 - closes listesindeki HER nokta için (period'dan sonra)
    bir RSI değeri üretir. Böylece 'son X dakikada eşiğin altına indi mi'
    kontrolü yapılabilir.
    Dönüş: rsi_values listesi (closes[period:] ile hizalı)
    """
    if len(closes) < period + 1:
        raise ValueError("RSI hesaplamak için yeterli veri yok")

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_values = []

    def rsi_from_avgs(ag, al):
        if al == 0:
            return 100.0
        rs = ag / al
        return round(100 - (100 / (1 + rs)), 2)

    rsi_values.append(rsi_from_avgs(avg_gain, avg_loss))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi_values.append(rsi_from_avgs(avg_gain, avg_loss))

    return rsi_values


def get_rsi_series(symbol: str, interval: str, limit: int = 200):
    closes = get_klines(symbol, interval, limit=limit)
    return calculate_rsi_series(closes, RSI_PERIOD)


def interval_minutes(interval: str) -> int:
    mapping = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
    return mapping[interval]


def dropped_below_in_window(rsi_series, threshold, window_minutes, interval):
    """Son 'window_minutes' dakikaya denk gelen mumlarda RSI eşiğin altına inmiş mi?"""
    step = interval_minutes(interval)
    candle_count = max(1, -(-window_minutes // step))  # yukarı yuvarla (ceil)
    recent = rsi_series[-candle_count:]
    return any(v <= threshold for v in recent), recent


def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID tanımlı değil (GitHub Secrets kontrol et).")
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


def check_symbol(symbol: str, state: dict):
    log.info(f"--- {symbol} kontrol ediliyor ---")

    rsi_5m_series = get_rsi_series(symbol, "5m")
    rsi_5m = rsi_5m_series[-1]
    log.info(f"{symbol} 5dk RSI14: {rsi_5m}")

    if rsi_5m > RSI_5M_THRESHOLD:
        return

    log.info(f"{symbol} 5dk RSI14 <= {RSI_5M_THRESHOLD} şartı sağlandı, 15dk kontrol ediliyor...")
    rsi_15m_series = get_rsi_series(symbol, "15m")
    hit_15m, recent_15m = dropped_below_in_window(
        rsi_15m_series, RSI_15M_THRESHOLD, WINDOW_15M_MINUTES, "15m"
    )
    log.info(f"{symbol} 15dk son {WINDOW_15M_MINUTES}dk RSI14 değerleri: {recent_15m} (eşik: {RSI_15M_THRESHOLD})")

    if not hit_15m:
        return

    log.info(f"{symbol} 15dk şartı sağlandı, 30dk kontrol ediliyor...")
    rsi_30m_series = get_rsi_series(symbol, "30m")
    hit_30m, recent_30m = dropped_below_in_window(
        rsi_30m_series, RSI_30M_THRESHOLD, WINDOW_30M_MINUTES, "30m"
    )
    log.info(f"{symbol} 30dk son {WINDOW_30M_MINUTES}dk RSI14 değerleri: {recent_30m} (eşik: {RSI_30M_THRESHOLD})")

    if not hit_30m:
        return

    if cooldown_active(state, symbol):
        log.info(f"{symbol} şartlar sağlandı ama cooldown aktif, sinyal tekrar gönderilmiyor.")
        return

    message = (
        f"🟢 <b>{symbol} AL SİNYALİ</b>\n\n"
        f"5dk RSI14: {rsi_5m}\n"
        f"15dk (son {WINDOW_15M_MINUTES}dk): {recent_15m}\n"
        f"30dk (son {WINDOW_30M_MINUTES}dk): {recent_30m}\n\n"
        f"Zaman: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    send_telegram_message(message)
    state[symbol] = datetime.now(timezone.utc).isoformat()


def main():
    log.info("RSI kademeli sinyal kontrolü başladı (tek seferlik çalışma).")
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
