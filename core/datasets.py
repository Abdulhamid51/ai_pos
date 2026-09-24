"""Tahlil uchun ma'lumot to'plamlari (dataset) — modelga shular ochiladi.

Har bir to'plam — bu bitta DataFrame. Muhim jihat: DataFrame **allaqachon
foydalanuvchi ruxsatiga qarab kesilgan** holda yig'iladi. Ya'ni model qanday
kod yozsa ham, ko'rmasligi kerak bo'lgan qatorga yeta olmaydi — cheklov kodda,
promptda emas.

Django modellari to'g'ridan-to'g'ri ochilmaydi: har bir to'plam qo'lda
tanlangan ustunlardan iborat. Sabab — parol, session yoki boshqa xizmat
ma'lumotlari tasodifan tushib qolmasin.
"""

from datetime import timedelta

import pandas as pd
from django.utils import timezone

from .models import Customer, PaymentMethod, Product, Sale, SaleItem, StockMovement

# Bitta to'plamga olinadigan eng ko'p qator soni — katta bazada ham xotira
# to'lib ketmasligi uchun.
MAX_ROWS = 20000

# Sotuvlar bo'yicha standart oyna: oxirgi 180 kun.
DEFAULT_DAYS = 180


# Har bir to'plamning ustunlari. Shu yerdan ham bo'sh DataFrame yasaladi, ham
# modelga beriladigan ro'yxat olinadi — ikkinchi nusxa saqlanmaydi.
COLUMNS = {
    "cheklar": [
        "chek_id", "raqam", "sana", "filial", "kassir", "mijoz", "summa_chegirmasiz",
        "chegirma", "jami", "qqs", "tan_narxi", "tolov_turi", "holat", "qaytarilgan", "foyda",
    ],
    "chek_qatorlari": [
        "chek_id", "chek_raqami", "sana", "filial", "mahsulot", "shtrix_kod",
        "birlik", "soni", "narx", "tan_narxi", "chegirma", "qaytarilgan_soni",
        "sof_soni", "tushum", "foyda",
    ],
    "mahsulotlar": [
        "mahsulot_id", "nomi", "shtrix_kod", "filial", "rang", "brend", "davlat",
        "birlik", "tan_narxi", "narx", "qoldiq", "minimal_qoldiq", "yaroqlilik",
        "olcham", "material", "jinsi", "mavsum", "faol", "foyda_ulushi", "qoldiq_kam",
    ],
    "mijozlar": [
        "mijoz_id", "ism", "telefon", "chegirma_foiz", "qarz", "qarz_chegarasi", "faol",
    ],
    "ombor_harakatlari": [
        "sana", "mahsulot", "filial", "turi", "ozgarish", "keyingi_qoldiq", "xodim", "izoh",
    ],
}


def _empty(columns):
    return pd.DataFrame({name: pd.Series(dtype="object") for name in columns})


# ---------------------------------------------------------------------------
# To'plamlarni yig'ish
# ---------------------------------------------------------------------------

def sales_frame(user):
    """Cheklar: bitta qator — bitta chek."""
    since = timezone.localdate() - timedelta(days=DEFAULT_DAYS)
    rows = list(
        Sale.objects.filter(branch__in=user.visible_branches(), created_at__date__gte=since)
        .values(
            "id", "number", "created_at", "branch__name", "cashier__username",
            "customer__full_name", "subtotal", "discount_amount", "total",
            "vat_amount", "cost_total", "payment_method", "status", "refunded_amount",
        )[:MAX_ROWS]
    )
    if not rows:
        return _empty(COLUMNS["cheklar"])

    frame = pd.DataFrame(rows).rename(columns={
        "id": "chek_id", "number": "raqam", "created_at": "sana",
        "branch__name": "filial", "cashier__username": "kassir",
        "customer__full_name": "mijoz", "subtotal": "summa_chegirmasiz",
        "discount_amount": "chegirma", "total": "jami", "vat_amount": "qqs",
        "cost_total": "tan_narxi", "payment_method": "tolov_turi",
        "status": "holat", "refunded_amount": "qaytarilgan",
    })

    frame["sana"] = pd.to_datetime(frame["sana"]).dt.tz_convert(timezone.get_current_timezone())
    for column in ["summa_chegirmasiz", "chegirma", "jami", "qqs", "tan_narxi", "qaytarilgan"]:
        frame[column] = frame[column].astype(float)

    labels = dict(PaymentMethod.choices)
    frame["tolov_turi"] = frame["tolov_turi"].map(labels).fillna("Noma'lum")
    frame["holat"] = frame["holat"].map(dict(Sale.Status.choices)).fillna("Noma'lum")
    frame["foyda"] = frame["jami"] - frame["qaytarilgan"] - frame["tan_narxi"]
    frame["mijoz"] = frame["mijoz"].fillna("")
    return frame


def sale_items_frame(user):
    """Chek qatorlari: bitta qator — chekda sotilgan bitta mahsulot."""
    since = timezone.localdate() - timedelta(days=DEFAULT_DAYS)
    rows = list(
        SaleItem.objects.filter(
            sale__branch__in=user.visible_branches(), sale__created_at__date__gte=since
        ).values(
            "sale_id", "sale__number", "sale__created_at", "sale__branch__name",
            "name", "barcode", "unit", "quantity", "price", "cost_price",
            "discount_amount", "returned_quantity",
        )[:MAX_ROWS]
    )
    if not rows:
        return _empty(COLUMNS["chek_qatorlari"])

    frame = pd.DataFrame(rows).rename(columns={
        "sale_id": "chek_id", "sale__number": "chek_raqami",
        "sale__created_at": "sana", "sale__branch__name": "filial",
        "name": "mahsulot", "barcode": "shtrix_kod", "unit": "birlik",
        "quantity": "soni", "price": "narx", "cost_price": "tan_narxi",
        "discount_amount": "chegirma", "returned_quantity": "qaytarilgan_soni",
    })

    frame["sana"] = pd.to_datetime(frame["sana"]).dt.tz_convert(timezone.get_current_timezone())
    for column in ["soni", "narx", "tan_narxi", "chegirma", "qaytarilgan_soni"]:
        frame[column] = frame[column].astype(float)

    frame["sof_soni"] = frame["soni"] - frame["qaytarilgan_soni"]
    # Qatorning sof tushumi: chegirma chiqarilgandan keyingi dona narxi × qolgan soni.
    unit_price = (frame["narx"] * frame["soni"] - frame["chegirma"]) / frame["soni"].replace(0, pd.NA)
    frame["tushum"] = (unit_price * frame["sof_soni"]).fillna(0.0)
    frame["foyda"] = frame["tushum"] - frame["tan_narxi"] * frame["sof_soni"]
    return frame


def products_frame(user):
    """Mahsulotlar: joriy narx va qoldiq."""
    rows = list(
        Product.objects.filter(branch__in=user.visible_branches())
        .values(
            "id", "name", "barcode", "branch__name", "color__name", "brand", "country",
            "unit", "cost_price", "price", "quantity", "min_quantity",
            "expiry_date", "size", "material", "gender", "season", "is_active",
        )[:MAX_ROWS]
    )
    if not rows:
        return _empty(COLUMNS["mahsulotlar"])

    frame = pd.DataFrame(rows).rename(columns={
        "id": "mahsulot_id", "name": "nomi", "barcode": "shtrix_kod",
        "branch__name": "filial", "color__name": "rang", "brand": "brend",
        "country": "davlat", "unit": "birlik", "cost_price": "tan_narxi",
        "price": "narx", "quantity": "qoldiq", "min_quantity": "minimal_qoldiq",
        "expiry_date": "yaroqlilik", "size": "olcham", "material": "material",
        "gender": "jinsi", "season": "mavsum", "is_active": "faol",
    })

    for column in ["tan_narxi", "narx", "qoldiq", "minimal_qoldiq"]:
        frame[column] = frame[column].astype(float)
    frame["rang"] = frame["rang"].fillna("")
    frame["yaroqlilik"] = pd.to_datetime(frame["yaroqlilik"], errors="coerce")
    frame["foyda_ulushi"] = frame["narx"] - frame["tan_narxi"]
    frame["qoldiq_kam"] = frame["qoldiq"] <= frame["minimal_qoldiq"]
    return frame


def customers_frame(user):
    """Mijozlar: chegirma va qarz."""
    company = user.company or (user.active_branch.company if user.active_branch else None)
    if not company:
        return _empty(COLUMNS["mijozlar"])

    rows = list(
        Customer.objects.filter(company=company)
        .values("id", "full_name", "phone", "discount_percent", "debt", "debt_limit", "is_active")[:MAX_ROWS]
    )
    if not rows:
        return _empty(COLUMNS["mijozlar"])

    frame = pd.DataFrame(rows).rename(columns={
        "id": "mijoz_id", "full_name": "ism", "phone": "telefon",
        "discount_percent": "chegirma_foiz", "debt": "qarz",
        "debt_limit": "qarz_chegarasi", "is_active": "faol",
    })
    for column in ["chegirma_foiz", "qarz", "qarz_chegarasi"]:
        frame[column] = frame[column].astype(float)
    return frame


def movements_frame(user):
    """Ombor harakatlari: qoldiq nega o'zgargani."""
    since = timezone.localdate() - timedelta(days=DEFAULT_DAYS)
    rows = list(
        StockMovement.objects.filter(
            product__branch__in=user.visible_branches(), created_at__date__gte=since
        ).values(
            "created_at", "product__name", "product__branch__name",
            "kind", "quantity", "balance_after", "user__username", "note",
        )[:MAX_ROWS]
    )
    if not rows:
        return _empty(COLUMNS["ombor_harakatlari"])

    frame = pd.DataFrame(rows).rename(columns={
        "created_at": "sana", "product__name": "mahsulot",
        "product__branch__name": "filial", "kind": "turi",
        "quantity": "ozgarish", "balance_after": "keyingi_qoldiq",
        "user__username": "xodim", "note": "izoh",
    })
    frame["sana"] = pd.to_datetime(frame["sana"]).dt.tz_convert(timezone.get_current_timezone())
    frame["turi"] = frame["turi"].map(dict(StockMovement.Kind.choices)).fillna("Noma'lum")
    for column in ["ozgarish", "keyingi_qoldiq"]:
        frame[column] = frame[column].astype(float)
    return frame


# ---------------------------------------------------------------------------
# Ro'yxat va tavsif
# ---------------------------------------------------------------------------

DATASETS = {
    "cheklar": {
        "loader": sales_frame,
        "description": (
            f"Sotuv cheklari (oxirgi {DEFAULT_DAYS} kun). Bitta qator — bitta chek: "
            "sana, filial, kassir, mijoz, jami summa, chegirma, tan narxi, foyda, to'lov turi."
        ),
    },
    "chek_qatorlari": {
        "loader": sale_items_frame,
        "description": (
            f"Cheklardagi mahsulot qatorlari (oxirgi {DEFAULT_DAYS} kun). Bitta qator — "
            "bitta chekda sotilgan bitta mahsulot: nomi, soni, narxi, tushum, foyda. "
            "Mahsulot kesimidagi savollar uchun shu to'plam ishlatiladi."
        ),
    },
    "mahsulotlar": {
        "loader": products_frame,
        "description": (
            "Ombordagi mahsulotlar: joriy narx, tan narx, qoldiq, minimal qoldiq, "
            "yaroqlilik muddati, brend, rang, o'lcham."
        ),
    },
    "mijozlar": {
        "loader": customers_frame,
        "description": "Mijozlar: ismi, telefoni, doimiy chegirmasi va qarzi.",
    },
    "ombor_harakatlari": {
        "loader": movements_frame,
        "description": (
            f"Ombor harakatlari (oxirgi {DEFAULT_DAYS} kun): kirim, sotuv, qaytarish, "
            "tuzatish, chiqim. Qoldiq nega o'zgarganini ko'rsatadi."
        ),
    },
}


def catalog():
    """Tizim ko'rsatmasiga qo'shiladigan ro'yxat: tavsif va ustun nomlari.

    Ustunlar shu yerda berilgani uchun model ko'p holatda `toplamni_korish`
    chaqirmasdan to'g'ridan-to'g'ri kod yozadi — bu bitta API chaqiruvini
    tejaydi.
    """
    lines = []
    for name, meta in DATASETS.items():
        lines.append(f"- {name}: {meta['description']}")
        lines.append(f"  ustunlar: {', '.join(COLUMNS[name])}")
    return "\n".join(lines)


def load(name, user):
    """To'plamni DataFrame ko'rinishida qaytaradi."""
    meta = DATASETS.get(name)
    if not meta:
        raise KeyError(name)
    return meta["loader"](user)


def describe(name, user, sample=3):
    """Ustunlar, turlari va bir nechta namuna qator — model kod yozishdan oldin ko'radi."""
    frame = load(name, user)
    columns = [
        {"ustun": column, "turi": str(frame[column].dtype)}
        for column in frame.columns
    ]
    # Uzun qiymatlar kesiladi — namuna qiymat formatini ko'rsatish uchun,
    # ma'lumotni uzatish uchun emas.
    rows = [
        {key: (value[:40] + "…" if len(value) > 40 else value) for key, value in row.items()}
        for row in frame.head(sample).astype(str).to_dict(orient="records")
    ]
    return {
        "toplam": name,
        "qatorlar_soni": int(len(frame)),
        "ustunlar": columns,
        "namuna": rows,
    }
