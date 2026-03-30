# Matriks Fiyat Botu

Gerçek zamanlı hisse fiyatları — Matriks WebSocket üzerinden.

## Kurulum

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
playwright install chromium
```

## Yapılandırma

```bash
cp .env.example .env
# .env dosyasını düzenle
```

## Çalıştırma

```bash
python bot.py
```

## Komutlar

| Komut | Açıklama |
|-------|----------|
| `/fiyat THYAO` | Anlık fiyat |
| `/liste` | Takip listesi |
| `/ekle THYAO` | Takip listesine ekle |
| `/çıkar THYAO` | Takip listesinden çıkar |
| `/alarm THYAO 300` | Fiyat alarmı kur |
| `/alarmlar` | Aktif alarmlar |
| `/hesap_ekle USER PASS` | Matriks hesabı ekle (admin) |
| `/hesaplar` | Hesap listesi (admin) |

Veya sadece sembol yaz: `THYAO`
