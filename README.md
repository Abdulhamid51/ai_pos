# ai_pos

Django asosidagi POS tizimi: kassa, ombor, mijozlar, hisobotlar va ma'lumot
ustida ishlaydigan AI yordamchi.

Stek: Django 5.2, SQLite, Pillow, pandas, openpyxl, Google Gemini API (`google-genai`).
Frontend — shablon va vanilla JS, qurish bosqichi (build step) yo'q.

## Ishga tushirish

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # SECRET_KEY va GEMINI_API_KEY ni to'ldiring
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Sayt: http://127.0.0.1:8000/ · Admin panel: http://127.0.0.1:8000/admin/

### Namunaviy ma'lumot

```bash
python manage.py demo_data --tozalash
```

Kirish: `direktor`, `kassir` yoki `sotuvchi` — parol `demo12345`.

### AI qismini yoqish

```bash
python manage.py ai_test           # kalit va modelni tekshirish
python manage.py index_products    # semantik qidiruv indeksini qurish
```

`.env` sozlamalari:

| O'zgaruvchi | Vazifasi | Standart |
|---|---|---|
| `GEMINI_API_KEY` | API kaliti | — |
| `GEMINI_MODEL` | suhbat modeli | `gemini-3.1-flash-lite` |
| `GEMINI_EMBED_MODEL` | vektor modeli | `gemini-embedding-2` |

Kalit bo'lmasa POS to'liq ishlaydi, faqat suhbat oynasi o'chiq holatda ko'rinadi.

## Arxitektura

Qatlamlar bir yo'nalishda bog'langan: ko'rinish → xizmat → model.

| Qatlam | Fayl | Mas'uliyati |
|---|---|---|
| Ma'lumot modeli | `core/models.py` | Kompaniya, filial, xodim, mahsulot, mijoz, chek, ombor jurnali |
| Biznes mantiq | `core/services.py` | Sotish, qaytarish, qarz to'lovi — tranzaksiya ichida |
| Statistika | `core/reporting.py` | Agregatlar: panel, hisobotlar va AI uchun umumiy |
| Qidiruv | `core/retrieval.py` | Semantik qidiruv va tayyor so'rovlar (RAG "R") |
| To'plamlar | `core/datasets.py` | Ruxsatga qarab kesilgan DataFrame'lar |
| Kod bajarish | `core/analysis.py` | Model yozgan pandas kodini tekshirib bajarish |
| Hujjatlar | `core/documents.py` | Nakladnoy faylini o'qish va mahsulotga moslash |
| AI | `core/ai.py` | Asboblar, suhbat sikli, xato tarjimasi |
| Ko'rinish | `core/views.py`, `core/forms.py` | HTTP, forma, ruxsat tekshiruvi |

`services.py` va `reporting.py` AI qatlamiga bog'liq emas: `ai.py` o'chirilsa ham
savdo qismi ishlashda davom etadi.

## Bajarilgan ishlar

### Ma'lumot modeli va biznes mantiq

- Kompaniya → filial → xodim iyerarxiyasi; bitta xodimga bir nechta filial
  biriktiriladi, faol filial almashtiriladi.
- `AbstractUser` asosidagi 6 rolli foydalanuvchi modeli; huquqlar model
  metodlarida (`can_sell`, `can_manage`, `visible_branches`).
- Filialning savdo turi (6 tur) mahsulot kartochkasidagi maydonlar to'plamini
  va interfeys urg'u rangini belgilaydi.
- Mahsulot: rang varianti (FK lug'at), rasm, tan/sotuv narxi, qoldiq, minimal
  qoldiq va savdo turiga xos 20+ ixtiyoriy maydon.
- Kassa: savat brauzerda, yakunlash serverda; naqd/karta/o'tkazma/qarz,
  chegirma, qaytim, chek chop etish.
- Chekni to'liq va qisman qaytarish; qaytarilgan miqdorga qarab chek holati
  qayta hisoblanadi.
- Mijoz: doimiy chegirma, qarz, qarz chegarasi, to'lovlar tarixi.
- Ombor jurnali: kirim, sotuv, qaytarish, tuzatish, chiqim — har bir yozuvda
  o'zgarish va keyingi qoldiq.
- Shtrix-kod generatsiyasi: kompaniya prefiksi va uzunligi bo'yicha, band
  bo'lmagan kod tanlanadi.
- Hisobotlar: tushum, foyda, rentabellik, o'rtacha chek, to'lov turlari,
  kassirlar va filiallar kesimi, qoldiq/muddat ogohlantirishlari.

### Interfeys

- Bitta CSS dizayn tizimi: ranglar CSS o'zgaruvchilarida, kunduzgi/tungi rejim
  va yig'iladigan yon menyu.
- Diagrammalar (Chart.js) ranglarni CSS o'zgaruvchilaridan oladi va rejim
  almashganda qayta chiziladi.
- Sozlamalar modali barcha sahifalarda — kontekst protsessori orqali
  (`core/context.py`).
- Tashqi frontend bog'liqliklari faqat ikkita, CDN orqali: Chart.js 4.4.3
  (panel va hisobotlar) va Tom Select 2.3.1 (tanlash maydonlari).
- Qolgan skriptlar — vanilla JS, IIFE ko'rinishida: kassa savati, mahsulot
  variantlari, mavzu almashtirish, chat.

### AI yordamchi (RAG)

- Bosh sahifa (`/`) — suhbat oynasi; boshqaruv paneli `/panel/` da.
- Model 6 ta asbobdan foydalanadi, hammasi faqat o'qiydi:

  | Asbob | Manba |
  |---|---|
  | `mahsulot_qidir` | vektor qidiruv + nom bo'yicha to'ldirish |
  | `savdo_hisoboti` | `reporting.summary`, `top_products`, `by_payment` |
  | `ombor_ogohlantirishlari` | `reporting.stock_alerts` |
  | `mijoz_qidir` | `Customer` bo'yicha ORM so'rovi |
  | `toplamni_korish` | to'plam ustunlari + 5 namuna qator |
  | `pandas_hisobla` | model yozgan pandas kodini bajarish |

- Semantik qidiruv: mahsulot matni `gemini-embedding-2` bilan 768 o'lchamli
  vektorga aylantiriladi, `ProductEmbedding` da saqlanadi, o'xshashlik
  Python'da kosinus bo'yicha hisoblanadi (alohida vektor bazasi ishlatilmagan).
- Erkin tahlil: model to'plam ustunlarini ko'radi, `def javob(df):` funksiyasini
  yozadi, natija tekshirilgan muhitda hisoblanadi.
- Natija hajmiga qarab yo'nalish: kichik natija modelga to'liq beriladi, katta
  natija foydalanuvchiga jadval bo'lib chiqadi, modelga faqat 5 qator namuna.
- Javob ostida manbalar ko'rsatiladi — qaysi asbob qanday argument bilan
  chaqirilgani.
- Suhbat tarixi Gemini tomonida (`previous_interaction_id`), sessiyada faqat
  oxirgi javob id'si, ko'rinadigan xabarlar brauzerning `sessionStorage` da.

### Nakladnoylar, qabul va ta'minotchilar

- Nakladnoy yuklash: rasm (jpg, png, webp), PDF, Excel (xlsx), CSV — 10 MB gacha.
  Ikki kirish nuqtasi: `/qabul/` sahifasidagi forma va chatdagi `/qabul`,
  `/sotuv` buyruqlari (fayl biriktirish yoki sudrab tashlash bilan).
- Rasm va PDF Gemini Files API orqali, Excel/CSV esa pandas bilan o'qilib matn
  sifatida yuboriladi; javob `ParsedWaybill` Pydantic sxemasiga majburlanadi.
- Har bir qator mahsulotga uch bosqichda moslanadi: shtrix-kod → aniq nom →
  semantik o'xshashlik (`MATCH_MIN_SCORE = 0.80`). Qanday moslangani sahifada
  belgilanadi: aniq / taxminiy / topilmadi.
- Ko'rib chiqish sahifasi: qatorlarni tahrirlash, mahsulotni qo'lda tanlash,
  qator qo'shish/o'chirish (Django inline formset), jonli summa.
- Qabulni tasdiqlash (`services.confirm_receipt`): qoldiq `apply_stock(KIRIM)`
  orqali oshadi, tan narxi yangilanadi, moslanmagan qator uchun yangi mahsulot
  yaratiladi, "qarzga" belgilansa ta'minotchi qarzi oshadi.
- Sotuvni tasdiqlash (`services.confirm_sale`): mavjud `checkout()` chaqiriladi —
  qoldiq, qarz chegarasi va huquq tekshiruvlari kassadagi bilan bir xil.
- Ta'minotchilar: ro'yxat, qo'shish/tahrirlash, nakladnoylar tarixi va qarz.
  Hujjatdagi nom mavjud ta'minotchiga avtomatik moslanadi (qo'shtirnoq va
  MChJ/OOO kabi huquqiy shakllar e'tiborga olinmaydi).
- Huquqlar: qabul va ta'minotchilar — direktor va ta'minotchi (`can_receive`),
  sotuv nakladnoyi — sotish huquqi borlar (`can_sell`).

### Suhbatlar tarixi

- `Conversation` / `Message` modellari: suhbatlar bazada, chap panelda ro'yxat,
  `/suhbat/<id>/` manzili, o'chirish.
- Har bir javob ostida token (kirish → chiqish), vaqt va asbob chaqiruvlari soni.

## Texnik yechimlar

Quyidagilar ataylab tanlangan yondashuvlar — sababi bilan.

**Pul va miqdor `Decimal`da.** `services.money()` va `amount_of()` har bir
kiritilgan qiymatni `quantize` bilan yaxlitlaydi; savdo hisob-kitobida float
ishlatilmaydi (float faqat pandas tahlilida, o'qish uchun).

**Qoldiq faqat `apply_stock()` orqali o'zgaradi.** Funksiya qoldiqni yangilaydi
va o'sha tranzaksiyada `StockMovement` yozadi, shuning uchun qoldiq va jurnal
bir-biridan ajralib qolmaydi.

**Chek qatori sotuv paytidagi holatni saqlaydi.** `SaleItem` da mahsulot nomi,
shtrix-kodi, narxi va tan narxi nusxa qilinadi; mahsulot keyin o'zgarsa yoki
o'chirilsa ham eski chek o'zgarmaydi (`on_delete=SET_NULL`).

**Sof ko'rsatkichlar SQL darajasida.** `reporting.py` dagi `NET_REVENUE` /
`NET_COST` ifodalari qaytarilgan miqdorni hisobdan chiqaradi, ya'ni agregatlar
Python siklisiz, bitta so'rovda olinadi.

**Shartli unikal cheklov.** `UniqueConstraint(fields=["branch", "barcode"],
condition=~Q(barcode=""))` — bitta filialda shtrix-kod takrorlanmaydi, lekin
bo'sh qiymatlar cheklovga tushmaydi.

**Savdo turiga qarab dinamik forma.** `TRADE_TYPE_FIELDS` lug'ati maydon
ro'yxatini beradi, forma `__init__` da keraksiz maydonlarni olib tashlaydi —
shablonda shart yozilmaydi.

**Rasm siqishda alfa kanali tekshiriladi.** RGBA rejimi shaffoflik bor degani
emas; `imaging.py` haqiqiy shaffof piksel bor-yo'qligini ko'rib, JPEG yoki PNG
tanlaydi.

**Rejim miltillashining oldi olingan.** `base.html` ning `<head>` qismidagi
kichik skript mavzu va menyu holatini CSS yuklanishidan oldin qo'llaydi.

**Ruxsat tekshiruvi kodda, promptda emas.** AI qatlami filiallarni har doim
`user.visible_branches()` dan oladi; model filialni argument sifatida tanlay
olmaydi, shuning uchun so'rov matni bilan chegaradan o'tib bo'lmaydi.

**AI qatlamining nosozligi POS'ga o'tmaydi.** `google.genai` importi funksiya
ichida; xatolar `AIError` ga aylantirilib, view'dan 503 bilan qaytadi.

**Moslik chegarasi o'lchov asosida.** Loyiha ma'lumotida o'lchangan qiymatlar:
mos natija 0.77–0.81, yaqin lekin begona 0.62, umuman begona 0.34–0.47.
Shu asosda `MIN_SCORE = 0.65` va eng yaxshi natijadan `SCORE_GAP = 0.12` dan
ortiq orqada qolganlar kesiladi.

**Model kodi uch qatlam himoya bilan bajariladi.** (1) AST tekshiruvi: `import`,
`while`, `__dunder__`, fayl yozadigan metodlar (`to_csv`, `read_*`) va xavfli
nomlar (`eval`, `open`, `getattr`) rad etiladi; (2) `__builtins__` o'rniga
27 nomli oq ro'yxat; (3) DataFrame allaqachon ruxsat bo'yicha kesilgan.

**Format prompt bilan emas, kod bilan ushlanadi.** Modelga "markdown jadval
yozma" deyish ishonchsiz bo'ldi, shuning uchun `chat.js` markdown jadvalni
haqiqiy `<table>` ga aylantiradi.

**Modeldan kelgan matn HTML sifatida ishlatilmaydi.** `chat.js` avval
`escapeHtml`, keyin cheklangan formatlash (qalin, kod, ro'yxat, jadval).

**Vektorlar matn hash'i bilan yangilanadi.** `ProductEmbedding.source_hash`
o'zgarmagan bo'lsa API qayta chaqirilmaydi.

**Model yozgan kod jurnalga tushadi** (`logger.info`) — nosozlikni tekshirish
uchun. Har bir API chaqiruvining tokenlari ham alohida yoziladi.

**Nakladnoy ikki bosqichli: qoralama → tasdiq.** AI o'qigan ma'lumot bazaga
faqat qoralama sifatida tushadi; qoldiq va chek foydalanuvchi tasdiqlagandan
keyin o'zgaradi. Tasdiqlash `select_for_update` bilan qulflanadi va bitta
tranzaksiyada bajariladi — biror qator xato bo'lsa, hech narsa qo'llanmaydi.

**Arifmetikani model emas, kod bajaradi.** Model qatordagi dona narxi va jami
summani alohida o'qiydi; dona narxi yo'q bo'lsa `jami / soni` kodda hisoblanadi,
ikkalasi bo'lsa `narx × soni ≠ jami` farqi ogohlantirish sifatida chiqadi.

**Excel sarlavhasiz o'qiladi** (`header=None`): haqiqiy nakladnoylarda tepada
rekvizitlar turadi, sarlavha qatorini model o'zi topadi.

**Ustun nomlari bitta manbada.** `datasets.COLUMNS` dan ham bo'sh DataFrame,
ham modelga beriladigan ro'yxat olinadi. Ustunlar tizim ko'rsatmasida bo'lgani
uchun model ko'pincha `toplamni_korish` siz kod yozadi — bitta API chaqiruvi
tejaladi (o'lchangan: 8 534 → 4 274 kirish tokeni).

## Rollar

| Rol | Huquqlari |
|---|---|
| Direktor | Hammasi: filiallar, xodimlar, kompaniya sozlamalari, barcha filial hisobotlari |
| Kassir, Sotuvchi, Agent | Kassa, o'z filiali mahsulotlari va hisobotlari |
| Ta'minotchi | Ombor va mahsulotlar |
| Mijoz | Ichki sahifalarga kira olmaydi |

## Tuzilishi

```
pos/                    sozlamalar va ildiz URL'lar
core/
  models.py             ma'lumot modeli
  services.py           sotish, qaytarish, qarz to'lovi
  reporting.py          statistika agregatlari
  ai.py                 asboblar va suhbat sikli
  retrieval.py          semantik qidiruv va tayyor so'rovlar
  datasets.py           pandas to'plamlari (ruxsat bo'yicha kesilgan)
  analysis.py           model kodini tekshirib bajarish
  documents.py          nakladnoy faylini o'qish va moslash
  imaging.py            rasm siqish
  context.py            umumiy shablon konteksti
  views.py, forms.py    ko'rinishlar va formalar
  management/commands/  demo_data, ai_test, index_products
templates/              base.html va umumiy qismlar
static/css/             style.css (dizayn tizimi), chat.css, waybill.css
static/js/              app.js, pos.js, products.js, dashboard.js, chat.js, waybill.js
```

## Testlar

```bash
python manage.py test
```

135 ta test (26 ta sinf): sotuv va qaytarish hisob-kitobi, qarzga sotish va
qarz chegarasi, qoldiq jurnali, rol va ruxsatlar, mahsulot/mijoz/xodim
ko'rinishlari, hisobot agregatlari, raqam kiritish formatlari, rasm siqish,
nakladnoy qabuli va sotuvi (tranzaksiya, qayta tasdiqlash, huquqlar), yuklash
va chat buyruqlari, pandas qum qutisi.

Gemini chaqiruvlari testlarda `mock.patch` bilan almashtiriladi — testlar
internet va API kalitisiz ishlaydi.

## Ma'lum cheklovlar

- Kod bajarish muhiti to'liq izolyatsiya emas: alohida jarayon, xotira va CPU
  cheklovi yo'q. `analysis.TIMEOUT` javobni bekor qiladi, lekin oqimni majburan
  to'xtatmaydi.
- Vektor indeksi qo'lda yangilanadi (`index_products`); mahsulot saqlanganda
  avtomatik yangilanmaydi.
- Javob oqimi (streaming) yo'q — pandas tahlilida 3–4 API chaqiruvi ketadi.
- AI so'rovlari uchun tezlik cheklovi (rate limit) qo'yilmagan.
- Tahlil oynasi 180 kun, to'plamga eng ko'pi 20 000 qator (`datasets.py`).
- Ma'lumotlar bazasi SQLite; bir vaqtda ko'p yozuvga mo'ljallanmagan.
- Nakladnoy tahlili sinxron: 5–10 soniya davomida HTTP so'rov band turadi.
- Ta'minotchiga to'lov (qarzni kamaytirish) hali yo'q — qarz faqat oshadi.
- Statik fayllarga versiya qo'yilmagan: yangilanishdan keyin brauzer eski JS/CSS ni
  keshdan olishi mumkin (Cmd+Shift+R). Ishlab chiqarishda
  `ManifestStaticFilesStorage` kerak.
