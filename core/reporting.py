"""Sotuv statistikasi — boshqaruv paneli va hisobotlar uchun umumiy hisob-kitob.

Barcha summalar qaytarilgan tovarlarni hisobga olgan holda ("sof") olinadi:
chekdagi qator uchun to'langan narx qaytarilmagan miqdorga ko'paytiriladi.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from .models import PaymentMethod, Product, Sale, SaleItem

MONEY = DecimalField(max_digits=18, decimal_places=2)
ZERO = Decimal("0")

# Qatorning sof tushumi: chegirma chiqarilgandan keyingi dona narxi × qolgan soni.
NET_REVENUE = ExpressionWrapper(
    (F("price") * F("quantity") - F("discount_amount"))
    * (F("quantity") - F("returned_quantity")) / F("quantity"),
    output_field=MONEY,
)
NET_COST = ExpressionWrapper(
    F("cost_price") * (F("quantity") - F("returned_quantity")), output_field=MONEY
)
NET_QUANTITY = ExpressionWrapper(
    F("quantity") - F("returned_quantity"), output_field=MONEY
)


def _sum(field):
    return Coalesce(Sum(field), ZERO, output_field=MONEY)


def items_of(sales):
    """Berilgan cheklarning qatorlari."""
    return SaleItem.objects.filter(sale__in=sales)


def summary(sales):
    """Umumiy ko'rsatkichlar: tushum, foyda, cheklar soni, o'rtacha chek."""
    totals = items_of(sales).aggregate(revenue=_sum(NET_REVENUE), cost=_sum(NET_COST))
    counts = sales.aggregate(
        checks=Count("pk"),
        refunded=_sum("refunded_amount"),
        vat=_sum("vat_amount"),
        discount=_sum("discount_amount"),
    )
    revenue = totals["revenue"]
    checks = counts["checks"]
    profit = revenue - totals["cost"]
    return {
        "revenue": revenue,
        "cost": totals["cost"],
        "profit": profit,
        "checks": checks,
        "avg_check": (revenue / checks) if checks else ZERO,
        "margin_pct": (profit / revenue * 100) if revenue else ZERO,
        "refunded": counts["refunded"],
        "vat": counts["vat"],
        "discount": counts["discount"],
    }


def daily_series(sales, days=14, until=None):
    """So'nggi `days` kun uchun kunlik tushum va foyda (bo'sh kunlar ham bor)."""
    until = until or timezone.localdate()
    since = until - timedelta(days=days - 1)

    rows = (
        items_of(sales.filter(created_at__date__range=(since, until)))
        .annotate(day=TruncDate("sale__created_at"))
        .values("day")
        .annotate(revenue=_sum(NET_REVENUE), cost=_sum(NET_COST))
    )
    by_day = {row["day"]: row for row in rows}

    labels, revenue, profit = [], [], []
    for offset in range(days):
        day = since + timedelta(days=offset)
        row = by_day.get(day)
        labels.append(day.strftime("%d.%m"))
        revenue.append(int(row["revenue"]) if row else 0)
        profit.append(int(row["revenue"] - row["cost"]) if row else 0)
    return {"days": labels, "revenue": revenue, "profit": profit}


def hourly_series(sales, start=9, end=22):
    """Ish vaqti davomida soatlar bo'yicha tushum."""
    buckets = {hour: 0 for hour in range(start, end)}
    rows = items_of(sales).values("sale__created_at").annotate(revenue=_sum(NET_REVENUE))
    for row in rows:
        hour = timezone.localtime(row["sale__created_at"]).hour
        hour = min(max(hour, start), end - 1)
        buckets[hour] += int(row["revenue"])
    return {
        "hours": [f"{h:02d}:00" for h in range(start, end)],
        "hourly": [buckets[h] for h in range(start, end)],
    }


def top_products(sales, limit=8):
    """Eng ko'p sotilgan mahsulotlar — soni va summasi bo'yicha."""
    rows = (
        items_of(sales)
        .values("name")
        .annotate(qty=_sum(NET_QUANTITY), total=_sum(NET_REVENUE), profit=_sum(NET_REVENUE) - _sum(NET_COST))
        .order_by("-qty")[:limit]
    )
    return [
        {
            "name": row["name"],
            "qty": int(row["qty"]),
            "total": int(row["total"]),
            "profit": int(row["profit"]),
        }
        for row in rows
    ]


def by_payment(sales):
    """To'lov turlari kesimida tushum."""
    rows = sales.values("payment_method").annotate(
        value=_sum("total") - _sum("refunded_amount"), checks=Count("pk")
    )
    found = {row["payment_method"]: row for row in rows}
    result = []
    for value, label in PaymentMethod.choices:
        row = found.get(value)
        if not row:
            continue
        result.append({
            "label": label,
            "value": int(row["value"]),
            "checks": row["checks"],
        })
    return sorted(result, key=lambda r: -r["value"])


def by_cashier(sales):
    """Kassirlar kesimida natija."""
    rows = (
        items_of(sales)
        .values("sale__cashier", "sale__cashier__username",
                "sale__cashier__first_name", "sale__cashier__last_name")
        .annotate(revenue=_sum(NET_REVENUE), profit=_sum(NET_REVENUE) - _sum(NET_COST),
                  checks=Count("sale", distinct=True))
        .order_by("-revenue")
    )
    result = []
    for row in rows:
        name = " ".join(filter(None, [row["sale__cashier__first_name"],
                                      row["sale__cashier__last_name"]]))
        result.append({
            "name": name or row["sale__cashier__username"] or "—",
            "revenue": int(row["revenue"]),
            "profit": int(row["profit"]),
            "checks": row["checks"],
            "avg_check": int(row["revenue"] / row["checks"]) if row["checks"] else 0,
        })
    return result


def by_branch(sales):
    """Filiallar kesimida natija."""
    rows = (
        items_of(sales)
        .values("sale__branch", "sale__branch__name")
        .annotate(revenue=_sum(NET_REVENUE), profit=_sum(NET_REVENUE) - _sum(NET_COST),
                  checks=Count("sale", distinct=True))
        .order_by("-revenue")
    )
    return [
        {
            "name": row["sale__branch__name"],
            "revenue": int(row["revenue"]),
            "profit": int(row["profit"]),
            "checks": row["checks"],
        }
        for row in rows
    ]


def stock_alerts(branches):
    """Diqqat talab qiladigan mahsulotlar: qoldiq kam va muddati o'tayotganlar."""
    today = timezone.localdate()
    products = Product.objects.filter(branch__in=branches, is_active=True)
    low = products.filter(quantity__lte=F("min_quantity")).select_related("branch", "color")
    expiring = products.filter(
        expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=30)
    ).select_related("branch").order_by("expiry_date")
    return {
        "low": low,
        "low_count": low.count(),
        "expiring": expiring,
        "expiring_count": expiring.count(),
    }


def sales_of(branches, since=None, until=None, **filters):
    """Filial va sana oralig'i bo'yicha cheklar to'plami."""
    sales = Sale.objects.filter(branch__in=branches)
    if since:
        sales = sales.filter(created_at__date__gte=since)
    if until:
        sales = sales.filter(created_at__date__lte=until)
    clean = {key: value for key, value in filters.items() if value not in (None, "")}
    return sales.filter(**clean) if clean else sales


def debt_summary(company):
    """Mijozlarning umumiy qarzi."""
    from .models import Customer

    rows = Customer.objects.filter(company=company, debt__gt=0).aggregate(
        total=_sum("debt"), people=Count("pk")
    )
    return {"total": rows["total"], "people": rows["people"]}


def day_bounds(request, default_days=13):
    """GET parametrlaridan sana oralig'ini o'qiydi (`dan` va `gacha`)."""
    today = timezone.localdate()

    def parse(name, fallback):
        raw = (request.GET.get(name) or "").strip()
        if not raw:
            return fallback
        try:
            return timezone.datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            return fallback

    until = parse("gacha", today)
    since = parse("dan", until - timedelta(days=default_days))
    if since > until:
        since, until = until, since
    return since, until
