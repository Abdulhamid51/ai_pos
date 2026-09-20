# ai_pos

Django asosidagi POS (savdo nuqtasi) tizimi: kassa, ombor, mijozlar va hisobotlar.
Keyingi bosqichda AI integratsiyasi qo'shiladi.

## Ishga tushirish

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # SECRET_KEY ni o'zingiznikiga almashtiring
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Sayt: http://127.0.0.1:8000/ · Admin panel: http://127.0.0.1:8000/admin/

### Namunaviy ma'lumot

Tizimni to'la ko'rish uchun demo kompaniya, xodimlar, mahsulotlar va bir oylik
sotuv tarixini yaratish mumkin:

```bash
python manage.py demo_data --tozalash
```

Kirish: `direktor`, `kassir` yoki `sotuvchi` — parol `demo12345`.

## Imkoniyatlar

- **Kassa** — shtrix-kod skaneri bilan ishlaydi, savat brauzerda yig'iladi,
  yakunlash serverda tekshiriladi. Naqd, karta, o'tkazma va qarzga sotish;
  chegirma, qaytim va chek chop etish.
- **Sotuvlar** — cheklar tarixi, davr va to'lov turi bo'yicha filtr,
  chek kartochkasi va tovarni to'liq yoki qisman qaytarish.
- **Mijozlar** — doimiy chegirma, qarz va qarz chegarasi, to'lovlar tarixi.
- **Ombor** — har bir mahsulot uchun kirim/chiqim/tuzatish jurnali;
  qoldiq faqat shu jurnal orqali o'zgaradi.
- **Mahsulotlar** — rang variantlari bilan qo'shish, tahrirlash, arxivlash.
  Ko'rinadigan maydonlar filialning savdo turiga moslashadi.
- **Hisobotlar** — tushum, foyda, rentabellik, to'lov turlari, kassirlar va
  filiallar kesimi, qoldiq va yaroqlilik muddati ogohlantirishlari.
- **Boshqaruv paneli** — bugungi ko'rsatkichlar va 14 kunlik dinamika.

## Rollar

| Rol | Huquqlari |
|---|---|
| Direktor | Hammasi: filiallar, xodimlar, kompaniya sozlamalari, barcha filial hisobotlari |
| Kassir, Sotuvchi, Agent | Kassa, o'z filiali mahsulotlari va hisobotlari |
| Ta'minotchi | Ombor va mahsulotlar |
| Mijoz | Kassaga kira olmaydi |

## Tuzilishi

- `pos/` — loyiha sozlamalari (`settings.py`, `urls.py`)
- `core/models.py` — kompaniya, filial, xodim, mahsulot, mijoz, chek, ombor
- `core/services.py` — sotish, qaytarish va qarz to'lovi mantiqi (tranzaksiya ichida)
- `core/reporting.py` — statistika hisob-kitobi (panel va hisobotlar uchun umumiy)
- `core/views.py`, `core/forms.py` — ko'rinishlar va formalar
- `templates/base.html` — umumiy sahifa qolipi
- `static/` — CSS/JS manbalari (`pos.js` — kassa, `dashboard.js` — diagrammalar)
- `.env` — maxfiy sozlamalar (git'ga tushmaydi)

## Testlar

```bash
python manage.py test
```
