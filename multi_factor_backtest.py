"""
Hangi Faktör Ani Fiyat Hareketlerinin En Büyük Etkeni?
==============================================================================
Üç göstergeyi karşılaştırır:
  1) Fonlama Oranı (Funding Rate)
  2) Long/Short Hesap Oranı
  3) Açık Pozisyon (Open Interest) değişimi

Her saat için, SONRAKİ 1 saatte fiyatın "ani yükseliş", "ani düşüş" ya da
"normal" olduğunu belirler, sonra bu üç grupta her göstergenin nasıl
davrandığına bakar. Hangi gösterge, ani hareketlerden ÖNCE en belirgin
şekilde "normalden farklı" davranıyorsa, o gösterge en güçlü öncü sinyal
adayıdır.

NOT: Bu KORELASYON gösterir, NEDENSELLİK değil. "En büyük etken" ifadesi
istatistiksel ilişki gücünü ifade eder, kesin bir sebep-sonuç kanıtı değildir.

Veri kaynağı: Binance Futures API (fapi.binance.com) - anahtar gerekmez.
Long/Short oranı ve Açık Pozisyon verisi Binance tarafından sadece SON ~30
GÜNLE sınırlı tutuluyor (bizim kontrolümüzde değil).
"""

import time
import requests
from datetime import datetime, timezone

FAPI_BASE = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"

BACKTEST_DAYS = 25  # long/short ve OI verisinin ~30 günlük limiti nedeniyle güvenli bir aralık
SUDDEN_MOVE_THRESHOLD_PCT = 1.0  # sonraki 1 saatte bu yüzdeden fazla hareket "ani" sayılır


def fetch_klines(symbol, interval, days):
    end_time = int(time.time() * 1000)
    start_time = end_time - days * 24 * 60 * 60 * 1000
    url = f"{FAPI_BASE}/fapi/v1/klines"
    all_klines = []
    cursor = start_time
    while cursor < end_time:
        params = {"symbol": symbol, "interval": interval, "startTime": cursor, "limit": 1000}
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_klines.extend(data)
        cursor = data[-1][0] + 1
        if len(data) < 1000:
            break
    return all_klines


def fetch_funding_rate_history(symbol, days):
    end_time = int(time.time() * 1000)
    start_time = end_time - days * 24 * 60 * 60 * 1000
    url = f"{FAPI_BASE}/fapi/v1/fundingRate"
    all_data = []
    cursor = start_time
    while cursor < end_time:
        params = {"symbol": symbol, "startTime": cursor, "limit": 1000}
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_data.extend(data)
        cursor = data[-1]["fundingTime"] + 1
        if len(data) < 1000:
            break
    return all_data


def fetch_long_short_ratio(symbol, days):
    url = f"{FAPI_BASE}/futures/data/globalLongShortAccountRatio"
    params = {"symbol": symbol, "period": "1h", "limit": 500}
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def fetch_open_interest_hist(symbol, days):
    url = f"{FAPI_BASE}/futures/data/openInterestHist"
    params = {"symbol": symbol, "period": "1h", "limit": 500}
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def value_at_or_before(sorted_items, time_key, value_key, target_time):
    result = None
    for item in sorted_items:
        if item[time_key] <= target_time:
            result = float(item[value_key])
        else:
            break
    return result


def main():
    print(f"=== {SYMBOL} verileri indiriliyor ===")
    kl_1h = fetch_klines(SYMBOL, "1h", BACKTEST_DAYS)
    closes = [float(k[4]) for k in kl_1h]
    closetimes = [k[6] for k in kl_1h]
    print(f"Fiyat: {len(kl_1h)} adet 1sa mum indirildi.")

    funding = fetch_funding_rate_history(SYMBOL, BACKTEST_DAYS)
    funding_sorted = sorted(funding, key=lambda x: x["fundingTime"])
    print(f"Fonlama oranı: {len(funding_sorted)} veri noktası indirildi.")

    ls_ratio = fetch_long_short_ratio(SYMBOL, BACKTEST_DAYS)
    ls_sorted = sorted(ls_ratio, key=lambda x: x["timestamp"])
    print(f"Long/Short oranı: {len(ls_sorted)} veri noktası indirildi (Binance limiti: ~30 gün).")

    oi_hist = fetch_open_interest_hist(SYMBOL, BACKTEST_DAYS)
    oi_sorted = sorted(oi_hist, key=lambda x: x["timestamp"])
    print(f"Açık Pozisyon (OI): {len(oi_sorted)} veri noktası indirildi (Binance limiti: ~30 gün).")

    if len(ls_sorted) < 10 or len(oi_sorted) < 10:
        print("\nHATA: Long/Short veya OI verisi çok az geldi, analiz güvenilir olmayabilir.")

    events = []
    for i in range(len(closes) - 1):
        t = closetimes[i]
        price_now = closes[i]

        # sonraki 1 saatteki fiyat değişimi (varsa)
        if i + 1 >= len(closes):
            continue
        price_next = closes[i + 1]
        pct_change = (price_next - price_now) / price_now * 100

        if pct_change >= SUDDEN_MOVE_THRESHOLD_PCT:
            category = "ANI_YUKSELIS"
        elif pct_change <= -SUDDEN_MOVE_THRESHOLD_PCT:
            category = "ANI_DUSUS"
        else:
            category = "NORMAL"

        funding_val = value_at_or_before(funding_sorted, "fundingTime", "fundingRate", t)
        ls_val = value_at_or_before(ls_sorted, "timestamp", "longShortRatio", t)
        oi_val = value_at_or_before(oi_sorted, "timestamp", "sumOpenInterest", t)

        events.append({
            "time": t,
            "category": category,
            "funding": funding_val,
            "ls_ratio": ls_val,
            "oi": oi_val,
        })

    # OI için "değişim yüzdesi" hesapla (önceki saate göre)
    for i in range(1, len(events)):
        if events[i]["oi"] is not None and events[i - 1]["oi"] is not None and events[i - 1]["oi"] != 0:
            events[i]["oi_change_pct"] = round(
                (events[i]["oi"] - events[i - 1]["oi"]) / events[i - 1]["oi"] * 100, 3
            )
        else:
            events[i]["oi_change_pct"] = None

    def group_stats(category):
        group = [e for e in events if e["category"] == category]
        fundings = [e["funding"] for e in group if e["funding"] is not None]
        ls_ratios = [e["ls_ratio"] for e in group if e["ls_ratio"] is not None]
        oi_changes = [e["oi_change_pct"] for e in group if e.get("oi_change_pct") is not None]

        return {
            "count": len(group),
            "avg_funding": round(sum(fundings) / len(fundings), 6) if fundings else None,
            "avg_ls_ratio": round(sum(ls_ratios) / len(ls_ratios), 4) if ls_ratios else None,
            "avg_oi_change": round(sum(oi_changes) / len(oi_changes), 3) if oi_changes else None,
        }

    normal_stats = group_stats("NORMAL")
    rise_stats = group_stats("ANI_YUKSELIS")
    fall_stats = group_stats("ANI_DUSUS")

    print(f"\n{'='*70}")
    print(f"SONUÇLAR (sonraki 1 saatte >=%{SUDDEN_MOVE_THRESHOLD_PCT} hareket = 'ani')")
    print(f"{'='*70}")

    print(f"\nNORMAL anlar ({normal_stats['count']} adet):")
    print(f"  Ort. Fonlama Oranı: {normal_stats['avg_funding']}")
    print(f"  Ort. Long/Short Oranı: {normal_stats['avg_ls_ratio']}")
    print(f"  Ort. OI Değişimi (%): {normal_stats['avg_oi_change']}")

    print(f"\nANİ YÜKSELİŞ öncesi anlar ({rise_stats['count']} adet):")
    print(f"  Ort. Fonlama Oranı: {rise_stats['avg_funding']}")
    print(f"  Ort. Long/Short Oranı: {rise_stats['avg_ls_ratio']}")
    print(f"  Ort. OI Değişimi (%): {rise_stats['avg_oi_change']}")

    print(f"\nANİ DÜŞÜŞ öncesi anlar ({fall_stats['count']} adet):")
    print(f"  Ort. Fonlama Oranı: {fall_stats['avg_funding']}")
    print(f"  Ort. Long/Short Oranı: {fall_stats['avg_ls_ratio']}")
    print(f"  Ort. OI Değişimi (%): {fall_stats['avg_oi_change']}")

    print(f"\n{'='*70}")
    print("SAPMA ANALİZİ (normalden ne kadar farklı - mutlak fark)")
    print(f"{'='*70}")

    def safe_diff(a, b):
        if a is None or b is None:
            return None
        return round(abs(a - b), 6)

    print("\nFonlama Oranı sapması:")
    print(f"  Yükseliş vs Normal: {safe_diff(rise_stats['avg_funding'], normal_stats['avg_funding'])}")
    print(f"  Düşüş vs Normal: {safe_diff(fall_stats['avg_funding'], normal_stats['avg_funding'])}")

    print("\nLong/Short Oranı sapması:")
    print(f"  Yükseliş vs Normal: {safe_diff(rise_stats['avg_ls_ratio'], normal_stats['avg_ls_ratio'])}")
    print(f"  Düşüş vs Normal: {safe_diff(fall_stats['avg_ls_ratio'], normal_stats['avg_ls_ratio'])}")

    print("\nOI Değişimi sapması:")
    print(f"  Yükseliş vs Normal: {safe_diff(rise_stats['avg_oi_change'], normal_stats['avg_oi_change'])}")
    print(f"  Düşüş vs Normal: {safe_diff(fall_stats['avg_oi_change'], normal_stats['avg_oi_change'])}")

    print("\nNot: Bu ham sapma değerleridir, farklı ölçeklerdeki göstergeleri")
    print("birebir karşılaştırmak yerine HER GÖSTERGENİN KENDİ İÇİNDE ne kadar")
    print("değiştiğine (yüzde olarak) bakmak daha doğru bir yorum sağlar.")


if __name__ == "__main__":
    main()
