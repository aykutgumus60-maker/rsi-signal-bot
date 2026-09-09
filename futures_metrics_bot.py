"""
BTC Futures Metrikleri Canlı Uyarı Botu
==============================================================================
Diğer botlardan bağımsız. Her 5 dakikada bir üç göstergeyi kontrol eder:
  1) Fonlama Oranı aşırı seviyede mi (pozitif veya negatif uçta)?
  2) Long/Short oranı aşırı bir uca mı gitti?
  3) Açık Pozisyon (OI) son 1 saatte anormal büyüklükte değişti mi?

Herhangi biri eşiği geçerse Telegram'a ayrı bir uyarı gönderir.
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone

FAPI_BASE = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"

# ============ EŞİKLER (backtest sonucuna göre ayarlanabilir) ============
FUNDING_RATE_EXTREME = 0.0005      # %0.05 üzeri (pozitif) ya da altı (negatif) "aşırı" sayılır
LONG_SHORT_RATIO_HIGH = 2.0        # bu değerin üzeri "aşırı long dolu"
LONG_SHORT_RATIO_LOW = 0.6         # bu değerin altı "aşırı short dolu"
OI_CHANGE_EXTREME_PCT = 3.0        # son 1 saatte OI bu yüzdeden fazla değiştiyse uyarı

SIGNAL_COOLDOWN_MINUTES = 120
STATE_FILE = "futures_metrics_state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("futures_metrics_bot")


def get_current_funding_rate(symbol):
    url = f"{FAPI_BASE}/fapi/v1/premiumIndex"
    resp = requests.get(url, params={"symbol": symbol}, timeout=15)
    resp.raise_for_status()
    return float(resp.json()["lastFundingRate"])


def get_current_long_short_ratio(symbol):
    url = f"{FAPI_BASE}/futures/data/globalLongShortAccountRatio"
    resp = requests.get(url, params={"symbol": symbol, "period": "5m", "limit": 1}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None
    return float(data[-1]["longShortRatio"])


def get_oi_change_pct(symbol):
    url = f"{FAPI_BASE}/futures/data/openInterestHist"
    resp = requests.get(url, params={"symbol": symbol, "period": "5m", "limit": 13}, timeout=15)  # ~1 saat
    resp.raise_for_status()
    data = resp.json()
    if len(data) < 2:
        return None
    oldest = float(data[0]["sumOpenInterest"])
    newest = float(data[-1]["sumOpenInterest"])
    if oldest == 0:
        return None
    return round((newest - oldest) / oldest * 100, 3)


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


def cooldown_active(state, key):
    last = state.get(key)
    if not last:
        return False
    last_dt = datetime.fromisoformat(last)
    elapsed_min = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
    return elapsed_min < SIGNAL_COOLDOWN_MINUTES


def maybe_alert(state, key, condition, message):
    if not condition:
        return
    if cooldown_active(state, key):
        log.info(f"{key} eşiği geçildi ama cooldown aktif, tekrar bildirim gönderilmiyor.")
        return
    send_telegram_message(message)
    state[key] = datetime.now(timezone.utc).isoformat()


def main():
    log.info("Futures metrik kontrolü başladı.")
    state = load_state()
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    try:
        funding = get_current_funding_rate(SYMBOL)
        log.info(f"Fonlama oranı: {funding}")
        maybe_alert(
            state, "funding_high",
            funding >= FUNDING_RATE_EXTREME,
            f"📈 <b>{SYMBOL} FONLAMA ORANI YÜKSEK</b>\n\nFonlama: {funding} (eşik: >={FUNDING_RATE_EXTREME})\n"
            f"Piyasa aşırı 'long' dolu olabilir, düzeltme riski.\n\nZaman: {now_str}"
        )
        maybe_alert(
            state, "funding_low",
            funding <= -FUNDING_RATE_EXTREME,
            f"📉 <b>{SYMBOL} FONLAMA ORANI DÜŞÜK (NEGATİF)</b>\n\nFonlama: {funding} (eşik: <=-{FUNDING_RATE_EXTREME})\n"
            f"Piyasa aşırı 'short' dolu olabilir, short squeeze riski.\n\nZaman: {now_str}"
        )
    except Exception as e:
        log.error(f"Fonlama oranı alınamadı: {e}")

    try:
        ls_ratio = get_current_long_short_ratio(SYMBOL)
        if ls_ratio is not None:
            log.info(f"Long/Short oranı: {ls_ratio}")
            maybe_alert(
                state, "ls_high",
                ls_ratio >= LONG_SHORT_RATIO_HIGH,
                f"⚠️ <b>{SYMBOL} LONG/SHORT ORANI AŞIRI YÜKSEK</b>\n\nOran: {ls_ratio} (eşik: >={LONG_SHORT_RATIO_HIGH})\n"
                f"Piyasa aşırı long dolu, kalabalık tek yönde.\n\nZaman: {now_str}"
            )
            maybe_alert(
                state, "ls_low",
                ls_ratio <= LONG_SHORT_RATIO_LOW,
                f"⚠️ <b>{SYMBOL} LONG/SHORT ORANI AŞIRI DÜŞÜK</b>\n\nOran: {ls_ratio} (eşik: <={LONG_SHORT_RATIO_LOW})\n"
                f"Piyasa aşırı short dolu, kalabalık tek yönde.\n\nZaman: {now_str}"
            )
    except Exception as e:
        log.error(f"Long/Short oranı alınamadı: {e}")

    try:
        oi_change = get_oi_change_pct(SYMBOL)
        if oi_change is not None:
            log.info(f"Son 1 saatte OI değişimi: %{oi_change}")
            maybe_alert(
                state, "oi_surge",
                abs(oi_change) >= OI_CHANGE_EXTREME_PCT,
                f"🔔 <b>{SYMBOL} AÇIK POZİSYON (OI) ANİ DEĞİŞTİ</b>\n\nSon 1 saatte değişim: %{oi_change} "
                f"(eşik: %{OI_CHANGE_EXTREME_PCT})\nYeni kaldıraçlı para piyasaya girdi/çıktı.\n\nZaman: {now_str}"
            )
    except Exception as e:
        log.error(f"OI verisi alınamadı: {e}")

    save_state(state)
    log.info("Kontrol tamamlandı.")


if __name__ == "__main__":
    main()
