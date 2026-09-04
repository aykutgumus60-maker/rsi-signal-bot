"""
BTC & PAXG - 5dk -> 15dk -> 30dk Kademeli RSI14 AL Sinyali Botu (v2)
=================================================================
GitHub Actions üzerinde her 5 dakikada bir TEK SEFERLİK çalışır.

Mantık (BTC için):
  1) 5dk RSI14 <= RSI_5M_THRESHOLD
  2) 15dk grafikte son 20dk içinde RSI14 <= RSI_15M_THRESHOLD
  3) 30dk grafikte son 40dk içinde RSI14 <= RSI_30M_THRESHOLD
  -> Telegram'a AL sinyali

Mantık (PAXG için - BTC ile aynı + EK DXY FİLTRESİ):
  1-3) Yukarıdaki aynı kademeli şartlar (PAXG kendi eşikleriyle)
  4) EK ŞART: DXY (Dolar Endeksi) 5dk RSI14 < DXY_RSI_MAX_THRESHOLD olmalı
     (backtest verisine göre: DXY RSI 70'in üzerindeyken PAXG sinyalinin
     kazanma oranı çok düşük çıktığı için bu durumda sinyal ENGELLENİR)

Aynı sinyalin tekrar tekrar gönderilmemesi için cooldown state.json'da saklanır.
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone

# ============ AYARLAR ============
BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
DXY_SYMBOL = "DX-Y.NYB"

RSI_PERIOD = 14

# Sembol bazlı eşikler
SYMBOL_SETTINGS = {
    "BTCUSDT": {
        "rsi_5m": 25,
        "rsi_15m": 30,
        "rsi_30m": 30,
        "use_dxy_filter": False,
    },
    "PAXGUSDT": {
        "rsi_5m": 25,
        "rsi_15m": 30,
        "rsi_30m": 30,
        "use_dxy_filter": True,
    },
}

WINDOW_15M_MINUTES = 20
WINDOW_30M_MINUTES = 40
DXY_RSI_MAX_THRESHOLD = 70  # DXY bu değerin ÜZERİNDEYSE PAXG sinyali engellenir

SIGNAL_COOLDOWN_MINUTES = 60
STATE_FILE = "state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rsi_bot")


def get_binance_klines(symbol, interval, limit=200):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return [float(k[4]) for k in data]


def get_dxy_closes(interval="5m", range_days=5):
    """DXY için son birkaç günlük kapanış verisini Yahoo'dan çeker (RSI hesaplamak için yeterli)."""
    url = YAHOO_CHART_URL.format(symbol=DXY_SYMBOL)
    params = {"interval": interval, "range": f"{range_days}d"}
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    result = data["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    return [c for c in closes if c is not None]


def calculate_rsi_series(closes, period=14):
    if len(closes) < period + 1:
        raise ValueError("RSI hesaplamak için yeterli veri yok")
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def rsi(ag, al):
        if al == 0:
            return 100.0
        rs = ag / al
        return round(100 - (100 / (1 + rs)), 2)

    values = [rsi(avg_gain, avg_loss)]
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        values.append(rsi(avg_gain, avg_loss))
    return values


def get_rsi_series(symbol, interval, limit=200):
    closes = get_binance_klines(symbol, interval, limit=limit)
    return calculate_rsi_series(closes, RSI_PERIOD)


def get_dxy_current_rsi():
    closes = get_dxy_closes("5m", range_days=5)
    series = calculate_rsi_series(closes, RSI_PERIOD)
    return series[-1]


def interval_minutes(interval):
    return {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}[interval]


def dropped_below_in_window(rsi_series, threshold, window_minutes, interval):
    step = interval_minutes(interval)
    candle_count = max(1, -(-window_minutes // step))
    recent = rsi_series[-candle_count:]
    return any(v <= threshold for v in recent), recent


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
    settings = SYMBOL_SETTINGS[symbol]
    log.info(f"--- {symbol} kontrol ediliyor ---")

    rsi_5m_series = get_rsi_series(symbol, "5m")
    rsi_5m = rsi_5m_series[-1]
    log.info(f"{symbol} 5dk RSI14: {rsi_5m}")

    if rsi_5m > settings["rsi_5m"]:
        return

    log.info(f"{symbol} 5dk şartı sağlandı ({rsi_5m} <= {settings['rsi_5m']}), 15dk kontrol ediliyor...")
    rsi_15m_series = get_rsi_series(symbol, "15m")
    hit_15m, recent_15m = dropped_below_in_window(
        rsi_15m_series, settings["rsi_15m"], WINDOW_15M_MINUTES, "15m"
    )
    log.info(f"{symbol} 15dk son {WINDOW_15M_MINUTES}dk RSI14: {recent_15m} (eşik: {settings['rsi_15m']})")
    if not hit_15m:
        return

    log.info(f"{symbol} 15dk şartı sağlandı, 30dk kontrol ediliyor...")
    rsi_30m_series = get_rsi_series(symbol, "30m")
    hit_30m, recent_30m = dropped_below_in_window(
        rsi_30m_series, settings["rsi_30m"], WINDOW_30M_MINUTES, "30m"
    )
    log.info(f"{symbol} 30dk son {WINDOW_30M_MINUTES}dk RSI14: {recent_30m} (eşik: {settings['rsi_30m']})")
    if not hit_30m:
        return

    # --- EK DXY FİLTRESİ (sadece PAXG için) ---
    dxy_rsi_value = None
    if settings["use_dxy_filter"]:
        try:
            dxy_rsi_value = get_dxy_current_rsi()
            log.info(f"{symbol} için DXY 5dk RSI14: {dxy_rsi_value} (üst sınır: {DXY_RSI_MAX_THRESHOLD})")
            if dxy_rsi_value >= DXY_RSI_MAX_THRESHOLD:
                log.info(f"{symbol} tüm RSI şartları sağlandı AMA DXY RSI çok yüksek "
                          f"({dxy_rsi_value} >= {DXY_RSI_MAX_THRESHOLD}) - sinyal ENGELLENDİ (backtest verisine göre).")
                return
        except Exception as e:
            log.error(f"DXY verisi alınamadı, filtre uygulanamadı: {e}. Güvenlik için sinyal gönderilmiyor.")
            return

    if cooldown_active(state, symbol):
        log.info(f"{symbol} şartlar sağlandı ama cooldown aktif, sinyal tekrar gönderilmiyor.")
        return

    dxy_line = f"\nDXY RSI14: {dxy_rsi_value} (filtre: <{DXY_RSI_MAX_THRESHOLD})" if dxy_rsi_value is not None else ""
    message = (
        f"🟢 <b>{symbol} AL SİNYALİ</b>\n\n"
        f"5dk RSI14: {rsi_5m}\n"
        f"15dk (son {WINDOW_15M_MINUTES}dk): {recent_15m}\n"
        f"30dk (son {WINDOW_30M_MINUTES}dk): {recent_30m}"
        f"{dxy_line}\n\n"
        f"Zaman: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    send_telegram_message(message)
    state[symbol] = datetime.now(timezone.utc).isoformat()


def main():
    log.info("RSI kademeli sinyal kontrolü başladı (tek seferlik çalışma).")
    state = load_state()

    for symbol in SYMBOL_SETTINGS:
        try:
            check_symbol(symbol, state)
        except Exception as e:
            log.error(f"{symbol} kontrol edilirken hata: {e}")

    save_state(state)
    log.info("Kontrol tamamlandı.")


if __name__ == "__main__":
    main()
