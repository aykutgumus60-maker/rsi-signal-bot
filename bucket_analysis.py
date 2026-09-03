"""
Kademeli RSI14 Stratejisi - RSI Aralık (Bucket) Bazlı Kazanma Oranı Analizi
==============================================================================
Sinyalleri giriş anındaki 5dk RSI14 değerine göre aralıklara (bucket) böler
ve HER ARALIK için ayrı ayrı kazanma oranı / ortalama getiri hesaplar.
Böylece "RSI 10-15 arasında mı, 25-30 arasında mı daha güvenilir" sorusuna
net, karşılaştırılabilir bir cevap verir.

90 günlük veri kullanır (BACKTEST_DAYS değiştirilebilir).
"""

import time
import requests
from datetime import datetime, timezone

BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"

SYMBOLS = ["BTCUSDT", "PAXGUSDT"]
RSI_PERIOD = 14

RSI_5M_THRESHOLD = 25
RSI_15M_THRESHOLD = 30
RSI_30M_THRESHOLD = 30
WINDOW_15M_MINUTES = 20
WINDOW_30M_MINUTES = 40

BACKTEST_DAYS = 90
HORIZONS_MINUTES = [60, 240, 1440]

# RSI aralıkları (bucket sınırları) - giriş anındaki 5dk RSI'ye göre
RSI_BUCKETS = [(0, 15), (15, 20), (20, 25), (25, 30)]


def fetch_all_klines(symbol, interval, days):
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


def analyze_symbol(symbol):
    print(f"\n=== {symbol} için veri indiriliyor ({BACKTEST_DAYS} gün) ===")
    kl_5m = fetch_all_klines(symbol, "5m", BACKTEST_DAYS)
    kl_15m = fetch_all_klines(symbol, "15m", BACKTEST_DAYS)
    kl_30m = fetch_all_klines(symbol, "30m", BACKTEST_DAYS)

    closes_5m = [float(k[4]) for k in kl_5m]
    closetime_5m = [k[6] for k in kl_5m]
    closes_15m = [float(k[4]) for k in kl_15m]
    closetime_15m = [k[6] for k in kl_15m]
    closes_30m = [float(k[4]) for k in kl_30m]
    closetime_30m = [k[6] for k in kl_30m]

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

        entry_price = closes_5m[RSI_PERIOD + i]
        signal = {
            "time": t5,
            "time_str": datetime.fromtimestamp(t5 / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "entry_price": entry_price,
            "rsi_5m": rsi_5m[i],
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

    return signals


def bucket_for(rsi_value):
    for lo, hi in RSI_BUCKETS:
        if lo <= rsi_value < hi:
            return f"{lo}-{hi}"
    return f"{RSI_BUCKETS[-1][1]}+"


def analyze_buckets(symbol, signals):
    print(f"\n{'='*70}")
    print(f"{symbol} - Toplam {len(signals)} sinyal - RSI ARALIK ANALİZİ")
    print(f"{'='*70}")

    for h in HORIZONS_MINUTES:
        key = f"chg_{h}m"
        label = {60: "1 SAAT SONRA", 240: "4 SAAT SONRA", 1440: "24 SAAT SONRA"}[h]
        print(f"\n--- {label} ---")

        for lo, hi in RSI_BUCKETS:
            bucket_signals = [s for s in signals if lo <= s["rsi_5m"] < hi and s[key] is not None]
            if not bucket_signals:
                print(f"  RSI [{lo}-{hi}): sinyal yok")
                continue
            wins = [s for s in bucket_signals if s[key] > 0]
            win_rate = round(len(wins) / len(bucket_signals) * 100, 1)
            avg_change = round(sum(s[key] for s in bucket_signals) / len(bucket_signals), 3)
            print(f"  RSI [{lo}-{hi}): {len(bucket_signals)} sinyal | "
                  f"Kazanma oranı: %{win_rate} | Ortalama getiri: %{avg_change}")


def main():
    for symbol in SYMBOLS:
        signals = analyze_symbol(symbol)
        analyze_buckets(symbol, signals)


if __name__ == "__main__":
    main()
