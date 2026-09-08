"""
BTC RSI14<=25 Anlarında Büyük Alım (>=1 BTC) Geçmiş Testi
==============================================================================
Yöntem:
  1) Son BACKTEST_DAYS günün 5dk RSI14 serisini hesaplar.
  2) RSI14 <= RSI_THRESHOLD olan her "sinyal anını" bulur.
  3) Karşılaştırma için, RASTGELE seçilmiş aynı sayıda "normal" (sinyalsiz)
     an da seçer (kontrol grubu).
  4) HER an için (hem sinyal hem kontrol), o 5 dakikalık pencerenin GERÇEK
     işlem detayını (aggTrades) Binance'ten çeker, >=LARGE_TRADE_BTC BTC'lik
     agresif (taker) alış işlemlerini filtreler, toplar.
  5) Sinyal anlarındaki ortalama büyük alım hacmini, kontrol grubunun
     ortalamasıyla karşılaştırır.

Bu, "RSI düşükken gerçekten daha fazla büyük alım oluyor mu" sorusuna
kanıta dayalı bir cevap verir.

NOT: aggTrades API'si 5 dakikalık bir pencerede en fazla 1000 işlem
döndürür. Çok yoğun dönemlerde (>1000 işlem/5dk) bazı işlemler kaçırılabilir
- bu bir yaklaşıklıktır (approximation), kesin/eksiksiz veri değildir.
"""

import time
import random
import requests
from datetime import datetime, timezone

BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
BINANCE_AGGTRADES_URL = "https://data-api.binance.vision/api/v3/aggTrades"

SYMBOL = "BTCUSDT"
RSI_PERIOD = 14
RSI_THRESHOLD = 25
LARGE_TRADE_BTC = 1.0

BACKTEST_DAYS = 14        # ne kadar geriye bakılacak
MAX_SAMPLES_PER_GROUP = 60  # her grup (sinyal/kontrol) için en fazla kaç an test edilecek (API yükünü sınırlar)


def fetch_klines(symbol, interval, days):
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


def get_large_buys_in_window(symbol, start_ms, end_ms, large_trade_btc):
    """Belirli bir zaman penceresindeki >=large_trade_btc agresif alış işlemlerini bulur."""
    params = {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000}
    try:
        resp = requests.get(BINANCE_AGGTRADES_URL, params=params, timeout=15)
        resp.raise_for_status()
        trades = resp.json()
    except Exception:
        return None

    large_buys = [
        t for t in trades
        if not t["m"] and float(t["q"]) >= large_trade_btc  # m=True ise satıcı taker, biz alıcı-taker istiyoruz -> m=False
    ]
    total_btc = sum(float(t["q"]) for t in large_buys)
    return {"count": len(large_buys), "total_btc": round(total_btc, 4), "checked": len(trades)}


def main():
    print(f"=== {SYMBOL} için {BACKTEST_DAYS} günlük 5dk veri indiriliyor ===")
    kl_5m = fetch_klines(SYMBOL, "5m", BACKTEST_DAYS)
    closes = [float(k[4]) for k in kl_5m]
    opentime = [k[0] for k in kl_5m]
    closetime = [k[6] for k in kl_5m]
    print(f"{len(kl_5m)} adet 5dk mum indirildi.")

    rsi_series = calc_rsi_series(closes, RSI_PERIOD)
    rsi_opentime = opentime[RSI_PERIOD:]
    rsi_closetime = closetime[RSI_PERIOD:]

    signal_indices = [i for i, r in enumerate(rsi_series) if r <= RSI_THRESHOLD]
    print(f"\nRSI <= {RSI_THRESHOLD} olan an sayısı: {len(signal_indices)}")

    if not signal_indices:
        print("Bu dönemde RSI hiç eşiğin altına inmemiş, karşılaştırma yapılamıyor.")
        return

    # örnek sayısını sınırla (API yükünü azaltmak için)
    if len(signal_indices) > MAX_SAMPLES_PER_GROUP:
        signal_indices = sorted(random.sample(signal_indices, MAX_SAMPLES_PER_GROUP))
        print(f"(Test süresini makul tutmak için {MAX_SAMPLES_PER_GROUP} örnekle sınırlandı)")

    # kontrol grubu: sinyal olmayan anlardan rastgele aynı sayıda seç
    non_signal_indices = [i for i in range(len(rsi_series)) if rsi_series[i] > RSI_THRESHOLD]
    control_indices = random.sample(non_signal_indices, min(len(signal_indices), len(non_signal_indices)))

    def analyze_group(indices, label):
        print(f"\n--- {label} grubu analiz ediliyor ({len(indices)} an) ---")
        results = []
        for idx in indices:
            start_ms = rsi_opentime[idx]
            end_ms = rsi_closetime[idx]
            data = get_large_buys_in_window(SYMBOL, start_ms, end_ms, LARGE_TRADE_BTC)
            if data is not None:
                results.append(data)
            time.sleep(0.15)  # Binance rate limitine takılmamak için küçük bekleme
        return results

    signal_results = analyze_group(signal_indices, f"SİNYAL (RSI<={RSI_THRESHOLD})")
    control_results = analyze_group(control_indices, "KONTROL (rastgele)")

    def summarize(results, label):
        if not results:
            print(f"{label}: veri yok")
            return
        avg_count = sum(r["count"] for r in results) / len(results)
        avg_btc = sum(r["total_btc"] for r in results) / len(results)
        pct_with_large_buy = sum(1 for r in results if r["count"] > 0) / len(results) * 100
        print(f"{label} ({len(results)} örnek):")
        print(f"  Ortalama büyük alım işlemi sayısı / 5dk: {round(avg_count, 2)}")
        print(f"  Ortalama büyük alım hacmi / 5dk: {round(avg_btc, 4)} BTC")
        print(f"  En az 1 büyük alım görülen pencere oranı: %{round(pct_with_large_buy, 1)}")

    print(f"\n{'='*70}")
    print("SONUÇ KARŞILAŞTIRMASI")
    print(f"{'='*70}")
    summarize(signal_results, f"RSI<={RSI_THRESHOLD} anları")
    summarize(control_results, "Rastgele (kontrol) anlar")


if __name__ == "__main__":
    main()
