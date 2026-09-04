"""
PAXG (Altın) - DXY (Dolar Endeksi) Ters Korelasyon Analizi
==============================================================================
PAXG ve DXY genelde ters yönde hareket eder (dolar güçlenirse altın zayıflar,
tersi de geçerli). Bu script:

  1) PAXG için BTC/PAXG botundaki AYNI kademeli RSI mantığıyla (5dk->15dk->30dk)
     AL sinyallerini bulur.
  2) Her PAXG sinyalinin geldiği ANDA, DXY'nin 5dk RSI14 değerinin ne olduğuna
     bakar.
  3) Sinyalleri DXY RSI seviyesine göre aralıklara (bucket) böler ve HER
     ARALIK için PAXG sinyalinin kazanma oranını hesaplar.

Böylece "DXY RSI kaçken PAXG sinyali daha güvenilir" sorusuna kanıta dayalı
cevap verir.

Veri kaynakları:
  - PAXG: Binance (data-api.binance.vision)
  - DXY: Yahoo Finance chart API (anahtar gerektirmez, ücretsiz)
        Not: Yahoo'nun 5dk/15dk/30dk verisi en fazla son 60 günü kapsar.
"""

import time
import requests
from datetime import datetime, timezone

BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"  # (artık kullanılmıyor, referans için bırakıldı)
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
DXY_SYMBOL = "DX-Y.NYB"
PAXG_SYMBOL = "PAXG-USD"

RSI_PERIOD = 14

# PAXG kademeli sinyal eşikleri (BTC/PAXG botundakiyle aynı mantık)
RSI_5M_THRESHOLD = 25
RSI_15M_THRESHOLD = 30
RSI_30M_THRESHOLD = 30
WINDOW_15M_MINUTES = 20
WINDOW_30M_MINUTES = 40

BACKTEST_DAYS = 60  # Yahoo intraday veri limiti nedeniyle 60 gün
HORIZONS_MINUTES = [60, 240, 1440]

# DXY RSI aralıkları (sinyal anındaki DXY 5dk RSI'sine göre)
DXY_RSI_BUCKETS = [(0, 30), (30, 45), (45, 55), (55, 70), (70, 100)]


def fetch_binance_klines(symbol, interval, days):
    end_time = int(time.time() * 1000)
    start_time = end_time - days * 24 * 60 * 60 * 1000
    all_klines = []
    cursor = start_time
    while cursor < end_time:
        params = {"symbol": symbol, "interval": interval, "startTime": cursor, "limit": 1000}
        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_klines.extend(data)
        cursor = data[-1][0] + 1
        if len(data) < 1000:
            break
    return all_klines


def fetch_yahoo_klines(symbol, interval, range_days=60):
    """Yahoo Finance chart API'den (timestamp_ms, close) çiftleri döndürür."""
    range_str = f"{range_days}d"
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {"interval": interval, "range": range_str}
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    times_ms, close_vals = [], []
    for t, c in zip(timestamps, closes):
        if c is not None:
            times_ms.append(t * 1000)
            close_vals.append(c)
    return times_ms, close_vals


def calc_rsi_series(closes, period=14):
    if len(closes) < period + 1:
        return []
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


def price_at_or_after(times, closes, target_time):
    for i, t in enumerate(times):
        if t >= target_time:
            return closes[i]
    return None


def value_at_or_before(times, values, target_time):
    result = None
    for t, v in zip(times, values):
        if t <= target_time:
            result = v
        else:
            break
    return result


def bucket_for(value, buckets):
    for lo, hi in buckets:
        if lo <= value < hi:
            return f"{lo}-{hi}"
    return f"{buckets[-1][1]}+"


def main():
    print("=== PAXG verisi indiriliyor (Yahoo Finance) ===")
    paxg_times_5m, paxg_closes_5m = fetch_yahoo_klines(PAXG_SYMBOL, "5m", BACKTEST_DAYS)
    paxg_times_15m, paxg_closes_15m = fetch_yahoo_klines(PAXG_SYMBOL, "15m", BACKTEST_DAYS)
    paxg_times_30m, paxg_closes_30m = fetch_yahoo_klines(PAXG_SYMBOL, "30m", BACKTEST_DAYS)
    print(f"PAXG: {len(paxg_times_5m)} adet 5dk veri noktası indirildi.")

    print("\n=== DXY verisi indiriliyor (Yahoo Finance) ===")
    dxy_times_5m, dxy_closes_5m = fetch_yahoo_klines(DXY_SYMBOL, "5m", BACKTEST_DAYS)
    print(f"DXY: {len(dxy_times_5m)} adet 5dk veri noktası indirildi.")

    if len(dxy_times_5m) < RSI_PERIOD + 1 or len(paxg_times_5m) < RSI_PERIOD + 1:
        print("HATA: Veri yetersiz, analiz yapılamıyor. Yahoo API kısıtlaması olabilir.")
        return

    dxy_rsi = calc_rsi_series(dxy_closes_5m, RSI_PERIOD)
    dxy_rsi_times = dxy_times_5m[RSI_PERIOD:]

    closes_5m = paxg_closes_5m
    closetime_5m = paxg_times_5m
    closes_15m = paxg_closes_15m
    closetime_15m = paxg_times_15m
    closes_30m = paxg_closes_30m
    closetime_30m = paxg_times_30m

    rsi_5m = calc_rsi_series(closes_5m, RSI_PERIOD)
    rsi_15m = calc_rsi_series(closes_15m, RSI_PERIOD)
    rsi_30m = calc_rsi_series(closes_30m, RSI_PERIOD)

    rsi_5m_times = closetime_5m[RSI_PERIOD:]
    rsi_15m_times = closetime_15m[RSI_PERIOD:]
    rsi_30m_times = closetime_30m[RSI_PERIOD:]

    signals = []
    last_signal_time = None
    COOLDOWN_MS = 60 * 60 * 1000

    for i, t5 in enumerate(rsi_5m_times):
        if rsi_5m[i] > RSI_5M_THRESHOLD:
            continue
        idx15 = [j for j, t in enumerate(rsi_15m_times) if t <= t5]
        if not idx15:
            continue
        window_start = t5 - WINDOW_15M_MINUTES * 60 * 1000
        recent15 = [rsi_15m[j] for j in idx15 if rsi_15m_times[j] >= window_start]
        if not recent15 or min(recent15) > RSI_15M_THRESHOLD:
            continue
        idx30 = [j for j, t in enumerate(rsi_30m_times) if t <= t5]
        if not idx30:
            continue
        window_start30 = t5 - WINDOW_30M_MINUTES * 60 * 1000
        recent30 = [rsi_30m[j] for j in idx30 if rsi_30m_times[j] >= window_start30]
        if not recent30 or min(recent30) > RSI_30M_THRESHOLD:
            continue
        if last_signal_time and (t5 - last_signal_time) < COOLDOWN_MS:
            continue

        # o anki DXY RSI değerini bul (lookahead yok)
        dxy_rsi_now = value_at_or_before(dxy_rsi_times, dxy_rsi, t5)
        if dxy_rsi_now is None:
            continue  # DXY verisi henüz o zamana ulaşmamış

        entry_idx = RSI_PERIOD + i
        entry_price = closes_5m[entry_idx]
        signal = {
            "time": t5,
            "time_str": datetime.fromtimestamp(t5 / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "entry_price": entry_price,
            "rsi_5m": rsi_5m[i],
            "dxy_rsi": dxy_rsi_now,
        }
        for h in HORIZONS_MINUTES:
            target_t = t5 + h * 60 * 1000
            future_price = price_at_or_after(closetime_5m, closes_5m, target_t)
            signal[f"chg_{h}m"] = (
                round((future_price - entry_price) / entry_price * 100, 3)
                if future_price is not None else None
            )
        signals.append(signal)
        last_signal_time = t5

    print(f"\n{'='*70}")
    print(f"PAXGUSDT - Toplam {len(signals)} sinyal - DXY RSI BAĞLAMI ANALİZİ")
    print(f"{'='*70}")

    for h in HORIZONS_MINUTES:
        key = f"chg_{h}m"
        label = {60: "1 SAAT SONRA", 240: "4 SAAT SONRA", 1440: "24 SAAT SONRA"}[h]
        print(f"\n--- {label} ---")
        for lo, hi in DXY_RSI_BUCKETS:
            bucket_signals = [s for s in signals if lo <= s["dxy_rsi"] < hi and s[key] is not None]
            if not bucket_signals:
                print(f"  DXY RSI [{lo}-{hi}): sinyal yok")
                continue
            wins = [s for s in bucket_signals if s[key] > 0]
            win_rate = round(len(wins) / len(bucket_signals) * 100, 1)
            avg_change = round(sum(s[key] for s in bucket_signals) / len(bucket_signals), 3)
            print(f"  DXY RSI [{lo}-{hi}): {len(bucket_signals)} sinyal | "
                  f"PAXG Kazanma oranı: %{win_rate} | Ortalama getiri: %{avg_change}")

    print(f"\n{'='*70}")
    print("DETAY LİSTE (tüm sinyaller, DXY RSI ile birlikte)")
    print(f"{'='*70}")
    for s in signals:
        print(f"  {s['time_str']} | PAXG giriş RSI: {s['rsi_5m']} | DXY RSI: {s['dxy_rsi']} | "
              f"1sa: %{s['chg_60m']} | 4sa: %{s['chg_240m']} | 24sa: %{s['chg_1440m']}")


if __name__ == "__main__":
    main()
