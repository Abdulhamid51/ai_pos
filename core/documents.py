"""Nakladnoy fayllarini o'qish: rasm, PDF, Excel, CSV → jadval qatorlari.

Ish tartibi:
  1. fayl turi aniqlanadi;
  2. rasm va PDF Gemini'ga fayl sifatida yuboriladi, Excel/CSV esa avval
     pandas bilan o'qilib, matn ko'rinishida beriladi (model ustun nomlarini
     bizning maydonlarga moslaydi);
  3. javob JSON sxemaga majburlanadi (`ParsedWaybill`);
  4. har bir qator filialdagi mahsulotga moslanadi: shtrix-kod → nom →
     semantik o'xshashlik.

Bu modul bazadagi qoldiqqa tegmaydi — faqat qoralama qatorlarni yaratadi.
Tasdiqlash `services.confirm_receipt` / `services.confirm_sale` da.
"""

import logging
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.db import transaction
from pydantic import BaseModel, Field

from . import retrieval
from .ai import AIError, client_or_error
from .models import Product, ProductEmbedding, WaybillItem

logger = logging.getLogger(__name__)

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
DOCUMENT_TYPES = {".pdf": "application/pdf"}
TABLE_TYPES = {".xlsx", ".csv"}
ALLOWED = set(IMAGE_TYPES) | set(DOCUMENT_TYPES) | TABLE_TYPES

MAX_FILE_SIZE = 10 * 1024 * 1024
# Excel/CSV dan modelga beriladigan eng ko'p qator.
MAX_TABLE_ROWS = 300
# Semantik moslashda "taxminiy" deb qabul qilinadigan eng past ball.
# Mahsulot qidiruvidagidan (0.65) yuqori: bu yerda xato qoldiqni buzadi.
MATCH_MIN_SCORE = 0.80


class DocumentError(Exception):
    """Faylni o'qib bo'lmadi — sabab foydalanuvchiga ko'rsatiladi."""


# ---------------------------------------------------------------------------
# Javob sxemasi
# ---------------------------------------------------------------------------

class ParsedItem(BaseModel):
    name: str = Field(description="Tovar nomi — hujjatda qanday yozilgan bo'lsa shunday.")
    barcode: str | None = Field(None, description="Shtrix-kod yoki artikul. Yo'q bo'lsa null.")
    quantity: float | None = Field(None, description="Soni yoki miqdori.")
    unit: str | None = Field(None, description="O'lchov birligi: dona, kg, litr, metr, quti.")
    price: float | None = Field(None, description="Bir birlik narxi. Hujjatda yo'q bo'lsa null.")
    total: float | None = Field(None, description="Qatorning jami summasi. Yo'q bo'lsa null.")


class ParsedWaybill(BaseModel):
    number: str | None = Field(None, description="Nakladnoy yoki hisob-faktura raqami.")
    date: str | None = Field(None, description="Hujjat sanasi, YYYY-MM-DD formatida.")
    supplier: str | None = Field(None, description="Yetkazib beruvchi (sotuvchi) tashkilot nomi.")
    items: list[ParsedItem] = Field(description="Tovar qatorlari. Jami/itogo qatorlarini qo'shma.")


INSTRUCTION = """Sen nakladnoy va hisob-fakturalardan tovar jadvalini o'qiysan.

Qoidalar:
- Faqat hujjatda yozilganini ol. Hech narsani taxmin qilma va hisoblama.
- Har bir tovar qatori — alohida element. "Jami", "Итого", "Всего" kabi
  yig'indi qatorlarini olma.
- Narx va jami summani hujjatdagidek alohida yoz: `price` — bir birlik narxi,
  `total` — qator summasi. Qaysi biri yo'q bo'lsa — null.
- Sonlarni bo'sh joy va valyuta belgisisiz yoz: "1 250 000 so'm" → 1250000.
- O'qib bo'lmaydigan qiymat — null.
"""


# ---------------------------------------------------------------------------
# Faylni modelga uzatish
# ---------------------------------------------------------------------------

def check_file(uploaded):
    """Yuklangan faylni turi va hajmi bo'yicha tekshiradi."""
    suffix = Path(uploaded.name).suffix.lower()
    if suffix not in ALLOWED:
        raise DocumentError(
            "Bu turdagi fayl qabul qilinmaydi. Mumkin: rasm (jpg, png, webp), PDF, Excel (xlsx), CSV."
        )
    if uploaded.size > MAX_FILE_SIZE:
        raise DocumentError("Fayl 10 MB dan katta.")
    return suffix


def _table_text(path, suffix):
    """Excel/CSV ni modelga beriladigan matnga aylantiradi."""
    try:
        if suffix == ".csv":
            frame = pd.read_csv(path, dtype=str, sep=None, engine="python")
        else:
            frame = pd.read_excel(path, dtype=str, header=None)
    except Exception as error:
        raise DocumentError(f"Jadvalni o'qib bo'lmadi: {error}")

    frame = frame.dropna(how="all").dropna(axis=1, how="all").fillna("")
    if frame.empty:
        raise DocumentError("Jadval bo'sh.")
    # Excel'da sarlavha odatda birinchi qatorda emas (tepada rekvizitlar bo'ladi),
    # shuning uchun header=None bilan o'qiladi va model sarlavhani o'zi topadi.
    return frame.head(MAX_TABLE_ROWS).to_csv(index=False, header=False, sep="|")


def _input_parts(client, path, suffix):
    """Fayl turiga qarab modelga beriladigan kirish qismlari."""
    if suffix in TABLE_TYPES:
        text = _table_text(path, suffix)
        return [{"type": "text", "text": "Quyidagi jadval — nakladnoy ('|' bilan ajratilgan):\n\n" + text}]

    uploaded = client.files.upload(file=str(path))
    part_type = "image" if suffix in IMAGE_TYPES else "document"
    return [
        {"type": "text", "text": "Ushbu nakladnoydagi tovar jadvalini o'qi."},
        {"type": part_type, "uri": uploaded.uri, "mime_type": uploaded.mime_type},
    ]


def read_document(path, suffix):
    """Faylni o'qib `ParsedWaybill` va o'lchovlarni qaytaradi."""
    client = client_or_error()
    started = time.monotonic()

    try:
        parts = _input_parts(client, path, suffix)
        interaction = client.interactions.create(
            model=settings.GEMINI_MODEL,
            system_instruction=INSTRUCTION,
            input=parts,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": ParsedWaybill.model_json_schema(),
            },
        )
    except DocumentError:
        raise
    except Exception as error:
        raise AIError(f"Faylni tahlil qilib bo'lmadi: {type(error).__name__}: {error}")

    try:
        parsed = ParsedWaybill.model_validate_json(interaction.output_text or "")
    except Exception as error:
        raise DocumentError(f"Model javobi sxemaga mos kelmadi: {error}")

    usage = getattr(interaction, "usage", None)
    tokens = (getattr(usage, "total_tokens", 0) or 0) if usage else 0
    elapsed = int((time.monotonic() - started) * 1000)
    logger.info("nakladnoy o'qildi: %s qator, %s token, %s ms", len(parsed.items), tokens, elapsed)
    return parsed, tokens, elapsed


# ---------------------------------------------------------------------------
# Qiymatlarni tozalash
# ---------------------------------------------------------------------------

UNITS = {
    "dona": "dona", "шт": "dona", "шт.": "dona", "sht": "dona", "pcs": "dona", "ta": "dona",
    "kg": "kg", "кг": "kg", "l": "litr", "л": "litr", "litr": "litr", "литр": "litr",
    "m": "metr", "м": "metr", "metr": "metr", "метр": "metr",
    "quti": "quti", "кор": "quti", "короб": "quti", "уп": "quti", "упак": "quti", "pack": "quti",
}


def _decimal(value, places="0.01"):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal(places))
    except (InvalidOperation, ValueError):
        return None


def _unit(value):
    return UNITS.get((value or "").strip().lower().rstrip("."), (value or "")[:20])


def _date(value):
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def _prices(item):
    """Dona narxini aniqlaydi. Arifmetikani model emas, kod bajaradi.

    Qaytadi: (narx, ogohlantirish_matni_yoki_None)
    """
    quantity = _decimal(item.quantity, "0.001")
    price = _decimal(item.price)
    total = _decimal(item.total)

    if price is None and total is not None and quantity:
        return (total / quantity).quantize(Decimal("0.01")), None

    if price is not None and total is not None and quantity:
        expected = (price * quantity).quantize(Decimal("0.01"))
        # 1 so'mgacha farq — yaxlitlash, e'tiborga olinmaydi.
        if abs(expected - total) > 1:
            return price, f"narx × soni = {expected}, hujjatda jami {total}"

    return price, None


# ---------------------------------------------------------------------------
# Mahsulotga moslash
# ---------------------------------------------------------------------------

def match_items(branch, items):
    """Har bir qatorni filialdagi mahsulotga moslaydi.

    Tartib: shtrix-kod (aniq) → nom (aniq) → semantik o'xshashlik (taxminiy).
    Qaytadi: [(product | None, usul, ball)] — `items` bilan bir xil tartibda.
    """
    products = list(Product.objects.filter(branch=branch, is_active=True).select_related("color"))
    by_barcode = {p.barcode: p for p in products if p.barcode}
    by_name = {}
    for product in products:
        by_name.setdefault(product.name.strip().lower(), product)
        by_name.setdefault(str(product).strip().lower(), product)

    results = [None] * len(items)
    pending = []
    for index, item in enumerate(items):
        code = (item.barcode or "").strip()
        if code and code in by_barcode:
            results[index] = (by_barcode[code], WaybillItem.Match.SHTRIX, 1.0)
            continue
        key = item.name.strip().lower()
        if key in by_name:
            results[index] = (by_name[key], WaybillItem.Match.NOM, 1.0)
            continue
        pending.append(index)

    # Qolganlar uchun semantik moslash — barcha nomlar bitta so'rovda vektorga aylanadi.
    vectors = {
        row.product_id: row.vector
        for row in ProductEmbedding.objects.filter(product__in=products)
    }
    if pending and vectors:
        try:
            query_vectors = retrieval.embed([items[i].name for i in pending])
        except Exception as error:
            logger.warning("semantik moslash o'tkazib yuborildi: %s", error)
            query_vectors = []

        for index, query_vector in zip(pending, query_vectors):
            best, best_score = None, 0.0
            for product in products:
                vector = vectors.get(product.pk)
                if vector:
                    score = retrieval._cosine(query_vector, vector)
                    if score > best_score:
                        best, best_score = product, score
            if best and best_score >= MATCH_MIN_SCORE:
                results[index] = (best, WaybillItem.Match.TAXMIN, round(best_score, 3))

    return [r or (None, WaybillItem.Match.YOQ, None) for r in results]


# ---------------------------------------------------------------------------
# Asosiy kirish nuqtasi
# ---------------------------------------------------------------------------

def parse_waybill(waybill):
    """Nakladnoy faylini o'qib, qoralama qatorlarini yaratadi.

    Qaytadi: {"items": qatorlar_soni, "matched": moslangan, "warnings": [...]}
    """
    suffix = Path(waybill.file.name).suffix.lower()
    parsed, tokens, elapsed = read_document(Path(waybill.file.path), suffix)

    if not parsed.items:
        raise DocumentError("Hujjatda tovar qatorlari topilmadi.")

    matches = match_items(waybill.branch, parsed.items)
    warnings = []

    with transaction.atomic():
        waybill.number = (parsed.number or "")[:64]
        waybill.doc_date = _date(parsed.date)
        waybill.supplier_name = (parsed.supplier or "")[:200]
        waybill.parse_tokens = tokens
        waybill.parse_ms = elapsed
        waybill.save()

        waybill.items.all().delete()
        for item, (product, how, score) in zip(parsed.items, matches):
            price, warning = _prices(item)
            if warning:
                warnings.append(f"{item.name}: {warning}")
            WaybillItem.objects.create(
                waybill=waybill,
                product=product,
                name=item.name[:250],
                barcode=(item.barcode or "")[:64],
                unit=_unit(item.unit),
                quantity=_decimal(item.quantity, "0.001") or Decimal("0"),
                price=price or Decimal("0"),
                matched_by=how,
                match_score=score,
            )

    matched = sum(1 for product, _, _ in matches if product)
    return {"items": len(parsed.items), "matched": matched, "warnings": warnings}
