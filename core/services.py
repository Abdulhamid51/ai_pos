"""Savdo amallari: kassadan sotish, qaytarish va qarz to'lovi.

Bu yerda ko'rinishlardan (views) mustaqil, tranzaksiya ichida bajariladigan
mantiq turadi — shunda bir xil qoida ham kassada, ham testlarda ishlaydi.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Max

from .models import (
    Customer,
    PaymentMethod,
    Product,
    Sale,
    SaleItem,
    StockMovement,
    apply_stock,
)

CENT = Decimal("0.01")
QUANTITY_STEP = Decimal("0.001")


class CheckoutError(Exception):
    """Sotuvni yakunlab bo'lmadi — sabab foydalanuvchiga ko'rsatiladi."""


def money(value):
    """Har qanday kiritilgan qiymatni ikki xonali pul summasiga keltiradi."""
    try:
        return Decimal(str(value or "0")).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise CheckoutError("Summa noto'g'ri kiritilgan.")


def amount_of(value):
    """Miqdor — uch xonagacha (0.5 kg, 1.25 metr kabi holatlar uchun)."""
    try:
        return Decimal(str(value or "0")).quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise CheckoutError("Miqdor noto'g'ri kiritilgan.")


def vat_of(total, percent):
    """QQS narx ichida hisoblanadi: jami summadan uning ulushi ajratiladi."""
    percent = Decimal(percent or 0)
    if percent <= 0:
        return Decimal("0")
    return (total * percent / (Decimal("100") + percent)).quantize(CENT, rounding=ROUND_HALF_UP)


def next_sale_number(branch):
    current = Sale.objects.filter(branch=branch).aggregate(top=Max("number"))["top"] or 0
    return current + 1


# ---------------------------------------------------------------------------
# Savat hisob-kitobi
# ---------------------------------------------------------------------------

def price_rows(branch, rows):
    """Savatdagi qatorlarni mahsulotlar bilan bog'laydi va tekshiradi.

    `rows` — [{"product": <id>, "quantity": <son>, "price": <ixtiyoriy narx>}]
    Javob: [(product, quantity, price)] ro'yxati.
    """
    if not rows:
        raise CheckoutError("Savat bo'sh.")

    wanted = {}
    for row in rows:
        try:
            pk = int(row.get("product"))
        except (TypeError, ValueError):
            raise CheckoutError("Mahsulot noto'g'ri ko'rsatilgan.")
        quantity = amount_of(row.get("quantity"))
        if quantity <= 0:
            raise CheckoutError("Mahsulot soni noldan katta bo'lishi kerak.")
        # Bir mahsulot bir necha marta skanerlansa — qatorlar qo'shiladi.
        previous = wanted.get(pk)
        price = row.get("price")
        wanted[pk] = (
            quantity + (previous[0] if previous else Decimal("0")),
            price if price not in (None, "") else (previous[1] if previous else None),
        )

    products = {
        p.pk: p
        for p in Product.objects.select_for_update().filter(pk__in=wanted, branch=branch)
    }

    priced = []
    for pk, (quantity, price) in wanted.items():
        product = products.get(pk)
        if product is None:
            raise CheckoutError("Mahsulot bu filialda topilmadi.")
        if not product.is_active:
            raise CheckoutError(f"\"{product.name}\" sotuvdan olingan.")
        priced.append((product, quantity, money(price) if price is not None else product.price))
    return priced


def totals_for(priced, discount_amount=Decimal("0"), customer=None):
    """Chek summalarini hisoblaydi: chegirmasiz summa, chegirma va jami."""
    subtotal = money(sum((price * quantity for _, quantity, price in priced), Decimal("0")))

    discount = money(discount_amount)
    if customer and customer.discount_percent:
        discount += money(subtotal * customer.discount_percent / Decimal("100"))

    if discount < 0:
        raise CheckoutError("Chegirma manfiy bo'lishi mumkin emas.")
    if discount > subtotal:
        raise CheckoutError("Chegirma chek summasidan katta bo'lishi mumkin emas.")

    return subtotal, discount, money(subtotal - discount)


# ---------------------------------------------------------------------------
# Sotish
# ---------------------------------------------------------------------------

@transaction.atomic
def checkout(*, user, branch, rows, payment_method, paid_amount=None,
             discount_amount=Decimal("0"), customer=None, note=""):
    """Savatni chekka aylantiradi: qoldiqni kamaytiradi, qarzni yangilaydi."""

    if not user.can_sell():
        raise CheckoutError("Sizda sotish huquqi yo'q.")
    if branch is None:
        raise CheckoutError("Faol filial tanlanmagan.")

    try:
        payment_method = PaymentMethod(int(payment_method))
    except (TypeError, ValueError):
        raise CheckoutError("To'lov turi tanlanmagan.")

    priced = price_rows(branch, rows)
    subtotal, discount, total = totals_for(priced, discount_amount, customer)

    company = branch.company
    if not company.allow_negative_stock:
        for product, quantity, _ in priced:
            if quantity > product.quantity:
                raise CheckoutError(
                    f"\"{product.name}\" qoldig'i yetarli emas "
                    f"({product.quantity:.0f} {product.get_unit_display()})."
                )

    if payment_method == PaymentMethod.QARZ:
        if customer is None:
            raise CheckoutError("Qarzga sotish uchun mijozni tanlang.")
        if not customer.debt_allows(total):
            raise CheckoutError(
                f"Mijozning qarz chegarasi {customer.debt_limit:.0f} — bu chek sig'maydi."
            )
        paid = Decimal("0")
        change = Decimal("0")
    elif payment_method == PaymentMethod.NAQD:
        paid = money(paid_amount if paid_amount not in (None, "") else total)
        if paid < total:
            raise CheckoutError("Berilgan pul chek summasidan kam.")
        change = money(paid - total)
    else:
        paid = total
        change = Decimal("0")

    sale = Sale.objects.create(
        branch=branch,
        number=next_sale_number(branch),
        cashier=user,
        customer=customer,
        subtotal=subtotal,
        discount_amount=discount,
        total=total,
        vat_amount=vat_of(total, company.vat_percent),
        cost_total=money(sum((p.cost_price * q for p, q, _ in priced), Decimal("0"))),
        payment_method=payment_method,
        paid_amount=paid,
        change_amount=change,
        note=note[:255],
    )

    # Chegirma qatorlarga summasiga proporsional taqsimlanadi — shunda
    # qaytarishda har bir qator haqiqiy to'langan narxda qaytariladi.
    remaining = discount
    last = len(priced) - 1
    for index, (product, quantity, price) in enumerate(priced):
        gross = money(price * quantity)
        if index == last:
            line_discount = remaining
        else:
            line_discount = money(discount * gross / subtotal) if subtotal else Decimal("0")
            remaining -= line_discount

        SaleItem.objects.create(
            sale=sale, product=product, name=str(product), barcode=product.barcode,
            unit=product.unit, quantity=quantity, price=price,
            cost_price=product.cost_price, discount_amount=line_discount,
        )
        apply_stock(
            product, -quantity, StockMovement.Kind.SOTUV,
            user=user, sale=sale, note=f"Chek №{sale.number}",
        )

    if payment_method == PaymentMethod.QARZ:
        Customer.objects.filter(pk=customer.pk).update(debt=customer.debt + total)
        customer.refresh_from_db(fields=["debt"])

    return sale


# ---------------------------------------------------------------------------
# Qaytarish
# ---------------------------------------------------------------------------

@transaction.atomic
def refund(*, sale, rows, user, note=""):
    """Chekdagi qatorlarni (to'liq yoki qisman) qaytaradi.

    `rows` — {sale_item_id: miqdor}. Qaytarilgan tovar omborga qaytadi,
    qarzga olingan chek bo'lsa mijozning qarzi shuncha kamayadi.
    """
    items = {item.pk: item for item in sale.items.select_related("product")}
    refunded = Decimal("0")
    touched = []

    for raw_id, raw_quantity in rows.items():
        try:
            item = items[int(raw_id)]
        except (KeyError, TypeError, ValueError):
            raise CheckoutError("Chek qatori topilmadi.")

        quantity = amount_of(raw_quantity)
        if quantity <= 0:
            continue
        if quantity > item.returnable_quantity:
            raise CheckoutError(
                f"\"{item.name}\" bo'yicha faqat {item.returnable_quantity:.0f} dona qaytarish mumkin."
            )

        item.returned_quantity += quantity
        item.save(update_fields=["returned_quantity"])
        refunded += money(item.unit_net_price * quantity)
        touched.append(item)

        if item.product_id:
            apply_stock(
                item.product, quantity, StockMovement.Kind.QAYTARISH,
                user=user, sale=sale, note=f"Chek №{sale.number} qaytarildi",
            )

    if not touched:
        raise CheckoutError("Qaytariladigan qator tanlanmadi.")

    sale.refunded_amount = money(sale.refunded_amount + refunded)
    # To'liq qaytarilganda yaxlitlash qoldig'i qolmasin.
    if sale.refunded_amount > sale.total:
        sale.refunded_amount = sale.total
    sale.recalc_status()
    if note:
        sale.note = (f"{sale.note} · {note}" if sale.note else note)[:255]
    sale.save(update_fields=["refunded_amount", "status", "note"])

    if sale.is_debt and sale.customer_id:
        customer = Customer.objects.select_for_update().get(pk=sale.customer_id)
        customer.debt = max(Decimal("0"), customer.debt - refunded)
        customer.save(update_fields=["debt"])

    return refunded


@transaction.atomic
def pay_debt(*, customer, amount, user, branch=None, note=""):
    """Mijoz qarzini to'laydi — qarz kamayadi va to'lov jurnalga yoziladi."""
    from .models import CustomerPayment

    amount = money(amount)
    if amount <= 0:
        raise CheckoutError("To'lov summasi noldan katta bo'lishi kerak.")

    locked = Customer.objects.select_for_update().get(pk=customer.pk)
    if amount > locked.debt:
        raise CheckoutError("To'lov qarzdan ko'p bo'lishi mumkin emas.")

    locked.debt = money(locked.debt - amount)
    locked.save(update_fields=["debt"])
    customer.debt = locked.debt

    return CustomerPayment.objects.create(
        customer=locked, branch=branch, user=user, amount=amount, note=note[:255]
    )
