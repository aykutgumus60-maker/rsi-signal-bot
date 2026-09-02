# BTC & PAXG Kademeli RSI14 Sinyal Botu (GitHub Actions)

## Mantık
Her sembol (BTCUSDT, PAXGUSDT) için ayrı ayrı, 5 dakikada bir:

1. **5dk RSI14 ≤ 30** ise → 15dk kontrolüne geç
2. **15dk grafikte son 20 dakika içinde** RSI14 herhangi bir mumda **≤ 35** olduysa → 30dk kontrolüne geç
3. **30dk grafikte son 40 dakika içinde** RSI14 herhangi bir mumda **≤ 35** olduysa → **Telegram'a AL sinyali**

Aynı sinyal 60 dakika içinde tekrar gönderilmez (cooldown, `state.json` içinde saklanır).

## Kurulum Adımları

### 1. GitHub'da yeni bir repo oluştur
- github.com → **New repository** → isim ver (örn: `rsi-signal-bot`) → **Private** seç (tokenlar için önerilir) → Create

### 2. Bu dosyaları repoya yükle
Bu klasördeki tüm dosyaları (`rsi_bot.py`, `.github/workflows/rsi_signal.yml`, `README.md`) repo'nun kök dizinine yükle.

**Web arayüzünden yüklemek istersen:**
- Repo sayfasında **Add file → Upload files**
- Dosyaları sürükle bırak (klasör yapısını korumak için `.github/workflows/rsi_signal.yml` dosyasını ayrı ayrı doğru path'e yüklemen gerekebilir — GitHub web arayüzü klasör yapısını sürükle-bırakla genelde koruyor)
- **Commit changes**

### 3. Telegram bilgilerini "Secrets" olarak ekle
- Repo → **Settings** → sol menüde **Secrets and variables → Actions**
- **New repository secret** ile iki tane ekle:
  - Name: `TELEGRAM_BOT_TOKEN` → Value: bot token'ın
  - Name: `TELEGRAM_CHAT_ID` → Value: chat id'n

⚠️ Token'ı asla direkt kod içine yazma — sadece Secrets üzerinden ekle, bu sayede repo'yu görüntüleyen kimse token'ı göremez.

### 4. Actions'ı etkinleştir
- Repo → **Actions** sekmesi → eğer bir uyarı çıkarsa **"I understand my workflows, go ahead and enable them"** butonuna bas

### 5. Test et
- **Actions** sekmesi → sol tarafta **RSI Kademeli Sinyal Botu** → **Run workflow** butonuyla manuel çalıştır
- Çalışma bitince (yeşil tik) üzerine tıklayıp logları incele, `check-rsi` adımında RSI değerlerini görebilirsin

### 6. Otomatik çalışma
Workflow artık her 5 dakikada bir otomatik tetiklenecek. Şartlar sağlandığında Telegram'a mesaj gelecek.

## Notlar
- GitHub Actions'ın **ücretsiz planında** private repolar için aylık 2000 dakika limit vardır. Bu bot her çalıştığında ~10-20 saniye sürer, 5 dakikada bir çalışsa bile aylık limitin çok altında kalır.
- Cron zamanlaması GitHub'ın yoğunluğuna göre birkaç dakika gecikebilir — bu Actions'ın genel bir kısıtlamasıdır, botun kendisinden kaynaklanmaz.
- `state.json` dosyası ilk çalıştırmada otomatik oluşacak, elle bir şey yapmana gerek yok.
