"""
BTC RSI14 + Büyük Alım (Büyük İşlem) Tespit Botu
==============================================================================
Diğer botlardan tamamen bağımsız çalışır.

Mantık:
  1) 5 dakikalık grafikte RSI14 hesaplanır.
  2) RSI14 <= RSI_THRESHOLD ise, o andaki SON 5 DAKİKANIN gerçek işlem
     defteri (trade tape) Binance'ten çekilir.
  3) Bu işlemler arasından "büyük" alım emirleri (agresif/taker alışları,
     LARGE_TRADE_BTC BTC ve üzeri tek işlemler) filtrelenir.
  4) Toplam büyük alım hacmi, işlem sayısı ve en büyük tekil işlem
     hesaplanıp Telegram mesajına eklenir.

ÖNEMLİ NOT: Bu, blok zinciri (on-chain) cüzdan takibi DEĞİLDİR. Bu veri,
Binance borsasının kendi genel/ücretsiz API'sinden gelen GERÇEK işlem
kayıtlarına dayanır - yani "Binance'te kim büyük miktarda BTC aldı" sorusuna
cevap verir, ama "hangi blok zinciri cüzdanı ne kadar BTC tuttu/transfer
etti" sorusuna cevap vermez (o, ücretli servisler - Arkham, Whale Alert vb.
- gerektirir).
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone

BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
BINANCE_TRADES_URL = "https://data-api.binance.vision/api/v3/trades"

SYMBOL = "BTCUSDT"
RSI_PERIOD = 14
RSI_THRESHOLD = 25

LARGE_TRADE_BTC = 1.0        # bu miktarın ÜZERİNDEKİ tek işlemler "büyük" sayılır
TRADE_LOOKBACK_MINUTES = 5   # son kaç dakikanın işlem defterine bakılacak

SIGNAL_COOLDOWN_MINUTES = 60
STATE_FILE = "whale_proxy_state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("whale_proxy_bot")


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


def get_recent_large_buys(symbol, lookback_minutes, large_trade_btc):
    """
    Son 1000 işlemi çeker, lookback_minutes içinde kalanları filtreler,
    içlerinden büyük agresif ALIŞ işlemlerini (taker buy) ayıklar.
    Not: isBuyerMaker == False => alıcı taraf agresif davrandı (piyasa fiyatından
    aldı) => bu bir "alım baskısı" göstergesidir.
    """
    params = {"symbol": symbol, "limit": 1000}
    resp = requests.get(BINANCE_TRADES_URL, params=params, timeout=15)
    resp.raise_for_status()
    trades = resp.json()

    cutoff_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - lookback_minutes * 60 * 1000
    recent = [t for t in trades if t["time"] >= cutoff_ms]

    large_buys = [
        t for t in recent
        if not t["isBuyerMaker"] and float(t["qty"]) >= large_trade_btc
    ]

    total_btc = sum(float(t["qty"]) for t in large_buys)
    largest = max((float(t["qty"]) for t in large_buys), default=0.0)

    return {
        "count": len(large_buys),
        "total_btc": round(total_btc, 4),
        "largest_trade_btc": round(largest, 4),
        "checked_trade_count": len(recent),
    }


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


def cooldown_active(state):
    last = state.get(SYMBOL)
    if not last:
        return False
    last_dt = datetime.fromisoformat(last)
    elapsed_min = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
    return elapsed_min < SIGNAL_COOLDOWN_MINUTES


def main():
    log.info("RSI + büyük alım tespit kontrolü başladı.")
    state = load_state()

    closes = get_closes(SYMBOL, "5m")
    rsi = calculate_rsi(closes, RSI_PERIOD)
    log.info(f"{SYMBOL} 5dk RSI14: {rsi}")

    if rsi > RSI_THRESHOLD:
        log.info(f"RSI eşiğin üzerinde ({rsi} > {RSI_THRESHOLD}), kontrol tamamlandı.")
        save_state(state)
        return

    if cooldown_active(state):
        log.info("RSI eşiği geçildi ama cooldown aktif, tekrar bildirim gönderilmiyor.")
        save_state(state)
        return

    log.info(f"RSI eşiği geçildi ({rsi} <= {RSI_THRESHOLD}), büyük alım verisi çekiliyor...")
    buy_data = get_recent_large_buys(SYMBOL, TRADE_LOOKBACK_MINUTES, LARGE_TRADE_BTC)
    log.info(f"Son {TRADE_LOOKBACK_MINUTES}dk: {buy_data['count']} büyük alım, "
              f"toplam {buy_data['total_btc']} BTC (kontrol edilen işlem: {buy_data['checked_trade_count']})")

    if buy_data["count"] > 0:
        buy_summary = (
            f"🐋 Son {TRADE_LOOKBACK_MINUTES}dk büyük alım tespiti (≥{LARGE_TRADE_BTC} BTC/işlem):\n"
            f"  - Büyük alım işlemi sayısı: {buy_data['count']}\n"
            f"  - Toplam büyük alım hacmi: {buy_data['total_btc']} BTC\n"
            f"  - En büyük tekil işlem: {buy_data['largest_trade_btc']} BTC"
        )
    else:
        buy_summary = (
            f"🐋 Son {TRADE_LOOKBACK_MINUTES}dk içinde {LARGE_TRADE_BTC} BTC ve üzeri "
            f"tekil bir alım tespit edilmedi."
        )

    message = (
        f"⚠️ <b>{SYMBOL} RSI14 DÜŞÜK + BÜYÜK ALIM KONTROLÜ</b>\n\n"
        f"5dk RSI14: {rsi} (eşik: {RSI_THRESHOLD} ve altı)\n\n"
        f"{buy_summary}\n\n"
        f"<i>Not: Bu, Binance borsasının işlem defterinden gelen veridir, "
        f"blok zinciri (on-chain) cüzdan takibi değildir.</i>\n\n"
        f"Zaman: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    send_telegram_message(message)
    state[SYMBOL] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    log.info("Kontrol tamamlandı.")


if __name__ == "__main__":
    main()
