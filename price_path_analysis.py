"""
Sinyal Sonrası Fiyat Yörüngesi Analizi
==============================================================================
Sabit zaman noktalarına (1sa/4sa/24sa) bakmak yerine, her sinyalden SONRA
fiyatın dakika dakika nasıl hareket ettiğini izler ve şunu bulur:

  1) Sinyalden sonra fiyat EN YÜKSEK noktaya (zirve) kaç dakikada ulaşıyor?
  2) O zirve, giriş fiyatına göre yüzde kaç yükseliş demek?
  3) Zirveye ulaşmadan önce fiyat ne kadar aşağı gidiyor (risk/drawdown)?

Sonuçta "sinyalden sonra ortalama X dakikada Y% yükseliş görülüyor" gibi
somut, eyleme dönüştürülebilir bir cevap verir.

Takip penceresi: TRACK_HOURS (varsayılan 24 saat) - bu süre içinde zirveye
ulaşılmazsa "24 saat içinde zirve yapmadı" olarak işaretlenir.
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
TRACK_HOURS = 24  # sinyalden sonra en fazla kaç saat takip edilecek

# Zirveye ulaşma süresini gruplamak için zaman dilimleri (dakika)
TIME_BUCKETS = [(0, 30), (30, 60), (60, 240), (240, 720), (720, 1440), (1440, 999999)]
TIME_BUCKET_LABELS = ["0-30dk", "30-60dk", "1-4sa", "4-12sa", "12-24sa", "24sa+ (zirve yapmadı)"]


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


def track_price_path(closetime_5m, closes_5m, entry_idx, entry_price, track_hours):
    """
    entry_idx'ten itibaren track_hours boyunca fiyatı izler.
    Zirveye (en yüksek fiyat) ulaşana kadar geçen süreyi ve zirve öncesi
    en düşük noktayı (drawdown) bulur.
    """
    entry_time = closetime_5m[entry_idx]
    cutoff_time = entry_time + track_hours * 60 * 60 * 1000

    window_prices = []
    window_times = []
    for i in range(entry_idx, len(closes_5m)):
        if closetime_5m[i] > cutoff_time:
            break
        window_prices.append(closes_5m[i])
        window_times.append(closetime_5m[i])

    if len(window_prices) < 2:
        return None  # veri yetersiz (sinyal, verinin sonuna çok yakın)

    max_price = max(window_prices)
    max_idx = window_prices.index(max_price)
    time_to_peak_min = (window_times[max_idx] - entry_time) / 60000
    peak_gain_pct = round((max_price - entry_price) / entry_price * 100, 3)

    # zirveye ulaşmadan önceki en düşük nokta (risk ölçümü)
    prices_before_peak = window_prices[:max_idx + 1]
    min_before_peak = min(prices_before_peak) if prices_before_peak else entry_price
    max_drawdown_pct = round((min_before_peak - entry_price) / entry_price * 100, 3)

    reached_peak_within_window = window_times[-1] >= cutoff_time or max_idx < len(window_prices) - 1

    return {
        "time_to_peak_min": round(time_to_peak_min, 1),
        "peak_gain_pct": peak_gain_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "reached_peak_within_window": max_idx < len(window_prices) - 1,  # zirve pencerenin ortasında mı, sonunda mı
    }


def time_bucket_for(minutes):
    for i, (lo, hi) in enumerate(TIME_BUCKETS):
        if lo <= minutes < hi:
            return TIME_BUCKET_LABELS[i]
    return TIME_BUCKET_LABELS[-1]


def analyze_symbol(symbol):
    print(f"\n=== {symbol} için {BACKTEST_DAYS} günlük veri indiriliyor ===")
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

    results = []
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

        entry_idx = RSI_PERIOD + i
        entry_price = closes_5m[entry_idx]
        path = track_price_path(closetime_5m, closes_5m, entry_idx, entry_price, TRACK_HOURS)
        if path is not None:
            path["time_str"] = datetime.fromtimestamp(t5 / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            results.append(path)
        last_signal_time = t5

    return results


def summarize(symbol, results):
    print(f"\n{'='*70}")
    print(f"{symbol} - Toplam {len(results)} sinyal - FİYAT YÖRÜNGESİ ANALİZİ")
    print(f"{'='*70}")

    if not results:
        print("  Sinyal bulunamadı.")
        return

    # zaman dilimine göre grupla
    bucket_counts = {label: 0 for label in TIME_BUCKET_LABELS}
    for r in results:
        bucket_counts[time_bucket_for(r["time_to_peak_min"])] += 1

    print("\n  Zirveye ulaşma süresi dağılımı:")
    for label in TIME_BUCKET_LABELS:
        count = bucket_counts[label]
        pct = round(count / len(results) * 100, 1)
        print(f"    {label}: {count} sinyal (%{pct})")

    avg_time_to_peak = round(sum(r["time_to_peak_min"] for r in results) / len(results), 1)
    avg_peak_gain = round(sum(r["peak_gain_pct"] for r in results) / len(results), 3)
    avg_drawdown = round(sum(r["max_drawdown_pct"] for r in results) / len(results), 3)

    sorted_times = sorted(r["time_to_peak_min"] for r in results)
    median_time_to_peak = sorted_times[len(sorted_times) // 2]

    print(f"\n  Ortalama zirveye ulaşma süresi: {avg_time_to_peak} dakika (~{round(avg_time_to_peak/60, 1)} saat)")
    print(f"  Medyan (ortanca) zirveye ulaşma süresi: {median_time_to_peak} dakika")
    print(f"  Ortalama zirve yükselişi (giriş fiyatına göre): %{avg_peak_gain}")
    print(f"  Ortalama zirve öncesi maksimum düşüş (risk): %{avg_drawdown}")

    print(f"\n  DETAY LİSTE:")
    for r in sorted(results, key=lambda x: x["time_str"]):
        print(f"    {r['time_str']} | Zirveye süre: {r['time_to_peak_min']}dk | "
              f"Zirve kazancı: %{r['peak_gain_pct']} | Zirve öncesi düşüş: %{r['max_drawdown_pct']}")


def main():
    for symbol in SYMBOLS:
        results = analyze_symbol(symbol)
        summarize(symbol, results)


if __name__ == "__main__":
    main()
