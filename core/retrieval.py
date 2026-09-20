"""Qidiruv qatlami — modelga beriladigan ma'lumot shu yerdan olinadi (RAG'ning "R" qismi).

Ikki xil qidiruv bor va ular turli savollarga xizmat qiladi:

  * semantik qidiruv (`search_products`) — matn bo'yicha: "sovuq ichimlik",
    "bolalar uchun kiyim". Mahsulot matni vektorga aylantirilib saqlanadi,
    savol ham vektorga aylantiriladi va eng yaqinlari topiladi.
  * aniq so'rov (`sales_report`, `stock_watch`, `find_customers`) — raqamlar
    uchun: bularni vektor emas, Django ORM hisoblaydi. Model hech qachon
    summani o'zi qo'shmaydi.

Barcha funksiyalar JSON'ga aylantirsa bo'ladigan oddiy dict/list qaytaradi —
natija to'g'ridan-to'g'ri modelga uzatiladi.
"""

import hashlib
import math
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from . import reporting
from .models import Customer, Product, ProductEmbedding

# Bitta so'rovda embedding olinadigan mahsulotlar soni.
EMBED_BATCH = 50

# Moslik chegarasi. Qiymatlar shu loyihaning ma'lumotida o'lchangan
# (gemini-embedding-2, 768 o'lcham):
#   mos natija          0.77 - 0.81
#   yaqin, lekin begona 0.62   ("sovuq ichimlik" -> futbolka)
#   umuman begona       0.34 - 0.47
# Shuning uchun pastki chegara 0.65. Bundan tashqari eng yaxshi natijadan
# ancha orqada qolganlar ham kesiladi — bitta aniq javob bo'lsa, unga
# o'xshamaganlar ro'yxatni to'ldirib yubormasin.
MIN_SCORE = 0.65
SCORE_GAP = 0.12


# ---------------------------------------------------------------------------
# Vektorlar
# ---------------------------------------------------------------------------

def product_text(product):
    """Mahsulotning semantik qidiruv uchun matni.

    Faqat ma'no tashiydigan maydonlar olinadi: narx va qoldiq bu yerga
    tushmaydi, chunki ular tez o'zgaradi va vektorni qayta hisoblashga majbur
    qilardi.
    """
    parts = [product.name, product.brand, product.country, product.description]
    parts += [
        product.material, product.size, product.dosage_form,
        product.active_ingredient, product.model_name,
    ]
    if product.color_id:
        parts.append(product.color.name)
    if product.gender:
        parts.append(product.get_gender_display())
    if product.season:
        parts.append(product.get_season_display())
    return " · ".join(part for part in parts if part)


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed(texts):
    """Matnlar ro'yxatini vektorlarga aylantiradi.

    Muhim: har bir matn alohida `Content` ichiga o'raladi. Aks holda API
    hammasini bitta umumiy vektorga birlashtirib yuboradi.
    """
    from .ai import AIError, client_or_error

    client = client_or_error()
    from google.genai import types

    try:
        result = client.models.embed_content(
            model=settings.GEMINI_EMBED_MODEL,
            contents=[types.Content(parts=[types.Part(text=text)]) for text in texts],
            config=types.EmbedContentConfig(output_dimensionality=settings.GEMINI_EMBED_DIM),
        )
    except Exception as error:
        raise AIError(f"Vektor olishda xato: {type(error).__name__}: {error}")

    return [list(item.values) for item in result.embeddings]


def index_products(branches=None, force=False):
    """Mahsulotlar uchun vektorlarni yaratadi yoki yangilaydi.

    Matni o'zgarmagan mahsulot o'tkazib yuboriladi — shuning uchun buyruqni
    qayta-qayta ishga tushirish arzon.
    """
    products = Product.objects.filter(is_active=True).select_related("color", "branch")
    if branches is not None:
        products = products.filter(branch__in=branches)

    existing = {
        row.product_id: row.source_hash
        for row in ProductEmbedding.objects.filter(product__in=products)
    }

    pending = []
    skipped = 0
    for product in products:
        text = product_text(product)
        if not text.strip():
            continue
        digest = _hash(text)
        if not force and existing.get(product.pk) == digest:
            skipped += 1
            continue
        pending.append((product, text, digest))

    indexed = 0
    for start in range(0, len(pending), EMBED_BATCH):
        chunk = pending[start:start + EMBED_BATCH]
        vectors = embed([text for _, text, _ in chunk])
        for (product, _, digest), vector in zip(chunk, vectors):
            ProductEmbedding.objects.update_or_create(
                product=product, defaults={"vector": vector, "source_hash": digest}
            )
            indexed += 1

    return {"indexed": indexed, "skipped": skipped}


def _cosine(a, b):
    """Ikki vektor orasidagi o'xshashlik: 1 — bir xil, 0 — aloqasiz."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------------------
# Qidiruvlar — model shu funksiyalarni chaqiradi
# ---------------------------------------------------------------------------

def _product_row(product, score=None):
    row = {
        "nomi": str(product),
        "filial": product.branch.name,
        "narx": int(product.price),
        "qoldiq": float(product.quantity),
        "birlik": product.unit,
    }
    if product.brand:
        row["brend"] = product.brand
    if product.expiry_date:
        row["yaroqlilik"] = product.expiry_date.strftime("%d.%m.%Y")
    if score is not None:
        row["moslik"] = round(score, 3)
    return row


def search_products(query, branches, limit=6):
    """Mahsulotlarni ma'no bo'yicha qidiradi, so'ng nom bo'yicha to'ldiradi.

    Vektor topilmasa (indeks hali yaratilmagan bo'lsa) — oddiy matn qidiruviga
    tushib qoladi, ya'ni yordamchi baribir javob beradi.
    """
    products = (
        Product.objects.filter(branch__in=branches, is_active=True)
        .select_related("branch", "color")
    )

    rows = []
    vectors = {
        row.product_id: row.vector
        for row in ProductEmbedding.objects.filter(product__in=products)
    }

    if vectors:
        try:
            query_vector = embed([query])[0]
        except Exception:
            query_vector = None

        if query_vector:
            scored = []
            for product in products:
                vector = vectors.get(product.pk)
                if vector:
                    scored.append((_cosine(query_vector, vector), product))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            if scored:
                cutoff = max(MIN_SCORE, scored[0][0] - SCORE_GAP)
                rows = [_product_row(p, score) for score, p in scored[:limit] if score >= cutoff]

    if len(rows) < limit:
        seen = {row["nomi"] for row in rows}
        keyword = products.filter(
            Q(name__icontains=query) | Q(brand__icontains=query) | Q(barcode=query)
        )[: limit - len(rows)]
        for product in keyword:
            if str(product) not in seen:
                rows.append(_product_row(product))

    return {"topildi": len(rows), "mahsulotlar": rows}


def sales_report(branches, period="bugun"):
    """Savdo ko'rsatkichlari. Hisobni ORM bajaradi, model faqat o'qiydi."""
    today = timezone.localdate()
    sales = reporting.sales_of(branches)

    if period == "kecha":
        day = today - timedelta(days=1)
        sales = sales.filter(created_at__date=day)
        label = f"kecha ({day:%d.%m.%Y})"
    elif period == "7kun":
        sales = sales.filter(created_at__date__gte=today - timedelta(days=6))
        label = "so'nggi 7 kun"
    elif period == "30kun":
        sales = sales.filter(created_at__date__gte=today - timedelta(days=29))
        label = "so'nggi 30 kun"
    elif period == "shu_oy":
        sales = sales.filter(created_at__date__gte=today.replace(day=1))
        label = f"shu oy ({today:%m.%Y})"
    else:
        sales = sales.filter(created_at__date=today)
        label = f"bugun ({today:%d.%m.%Y})"

    stats = reporting.summary(sales)
    return {
        "davr": label,
        "tushum": int(stats["revenue"]),
        "foyda": int(stats["profit"]),
        "cheklar": stats["checks"],
        "ortacha_chek": int(stats["avg_check"]),
        "chegirma": int(stats["discount"]),
        "qaytarilgan": int(stats["refunded"]),
        "top_mahsulotlar": reporting.top_products(sales, limit=5),
        "tolov_turlari": reporting.by_payment(sales),
    }


def stock_watch(branches, limit=10):
    """Diqqat talab qiladigan tovarlar: qoldig'i kam va muddati yaqinlar."""
    alerts = reporting.stock_alerts(branches)
    return {
        "kam_qolgan_soni": alerts["low_count"],
        "kam_qolganlar": [
            {"nomi": str(p), "qoldiq": float(p.quantity), "minimal": float(p.min_quantity),
             "filial": p.branch.name}
            for p in alerts["low"][:limit]
        ],
        "muddati_yaqin_soni": alerts["expiring_count"],
        "muddati_yaqinlar": [
            {"nomi": str(p), "yaroqlilik": p.expiry_date.strftime("%d.%m.%Y"),
             "qoldiq": float(p.quantity), "filial": p.branch.name}
            for p in alerts["expiring"][:limit]
        ],
    }


def find_customers(company, query="", limit=8):
    """Mijozlarni ism yoki telefon bo'yicha topadi. Bo'sh so'rov — qarzdorlar."""
    customers = Customer.objects.filter(company=company, is_active=True)
    if query:
        customers = customers.filter(Q(full_name__icontains=query) | Q(phone__icontains=query))
    else:
        customers = customers.filter(debt__gt=0).order_by("-debt")

    rows = [
        {
            "ism": c.full_name,
            "telefon": c.phone,
            "qarz": int(c.debt),
            "chegirma_foiz": float(c.discount_percent),
        }
        for c in customers[:limit]
    ]
    return {"topildi": len(rows), "mijozlar": rows}
