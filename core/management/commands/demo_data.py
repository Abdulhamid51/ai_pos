"""Namunaviy ma'lumot: kompaniya, filiallar, xodimlar, mahsulotlar va sotuvlar.

Tizimni birinchi marta ko'rish uchun qulay — barcha sahifalar to'la ishlaydi.

    python manage.py demo_data --tozalash
"""

import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import (
    Branch,
    Color,
    Company,
    Customer,
    PaymentMethod,
    Product,
    Sale,
    SaleItem,
    StockMovement,
    TradeType,
    User,
)

PAROL = "demo12345"

COLORS = [
    ("Qora", "#151b23"), ("Oq", "#f3f6fa"), ("Ko'k", "#1f5fa9"),
    ("Qizil", "#d8453f"), ("Yashil", "#1baf7a"), ("Kulrang", "#8695a8"),
]

# (nomi, tan narxi, sotuv narxi, birligi, boshlang'ich qoldiq, brend)
OZIQ_OVQAT = [
    ("Non (oddiy)", 3000, 4500, "dona", 120, ""),
    ("Sut 1L", 9000, 13000, "dona", 80, "Nestle"),
    ("Tuxum (10 dona)", 16000, 22000, "quti", 60, ""),
    ("Shakar 1kg", 11000, 14500, "kg", 90, ""),
    ("Guruch 1kg", 14000, 19000, "kg", 70, "Lazer"),
    ("Coca-Cola 1L", 9000, 12000, "dona", 100, "Coca-Cola"),
    ("Choy (100g)", 12000, 17000, "dona", 45, "Ahmad"),
    ("Yog' 1L", 21000, 27000, "dona", 55, "Oltin Tomchi"),
    ("Makaron 400g", 6000, 9000, "dona", 85, ""),
    ("Tuz 1kg", 2000, 3500, "kg", 60, ""),
]

KIYIM = [
    ("Futbolka Polo", 60000, 95000, "dona", 25, "Polo"),
    ("Jinsi shim", 140000, 220000, "dona", 18, "Levi's"),
    ("Ko'ylak (klassik)", 95000, 160000, "dona", 20, "Zara"),
    ("Krossovka", 210000, 340000, "dona", 14, "Nike"),
    ("Sviter", 110000, 180000, "dona", 16, "H&M"),
    ("Kurtka (demisezon)", 260000, 420000, "dona", 10, "Zara"),
]

MIJOZLAR = [
    ("Dilshod Aliyev", "+998901112233", 5, 0),
    ("Nodira Karimova", "+998933334455", 0, 500000),
    ("Sardor Yo'ldoshev", "+998975556677", 3, 1000000),
    ("Gulnora Rasulova", "+998911234567", 0, 0),
]


class Command(BaseCommand):
    help = "Namunaviy kompaniya, xodimlar, mahsulotlar va sotuvlarni yaratadi."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tozalash", action="store_true",
            help="Avval eski namunaviy ma'lumotlarni o'chirish.",
        )
        parser.add_argument(
            "--kunlar", type=int, default=30,
            help="Necha kunlik sotuv tarixi yaratilsin (standart: 30).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        rnd = random.Random(2026)

        if options["tozalash"]:
            self._wipe()

        company, _ = Company.objects.get_or_create(
            name="Demo Savdo",
            defaults={
                "legal_name": "\"Demo Savdo\" MChJ",
                "tin": "301234567",
                "phone": "+998 71 200 00 00",
                "address": "Toshkent sh., Amir Temur ko'chasi 1",
                "barcode_prefix": "200",
                "vat_percent": Decimal("12"),
                "receipt_footer": "Xaridingiz uchun rahmat!",
            },
        )

        oziq = self._branch(company, "Markaziy do'kon", TradeType.OZIQ_OVQAT, main=True)
        kiyim = self._branch(company, "Chilonzor · kiyim", TradeType.KIYIM)

        for name, hex_value in COLORS:
            Color.objects.get_or_create(name=name, defaults={"hex": hex_value})

        direktor = self._user(company, "direktor", "Anvar", "Qodirov", User.Role.DIREKTOR, [oziq, kiyim])
        kassir = self._user(company, "kassir", "Malika", "Yusupova", User.Role.KASSIR, [oziq])
        sotuvchi = self._user(company, "sotuvchi", "Jasur", "Islomov", User.Role.SOTUVCHI, [kiyim])

        oziq_products = self._products(oziq, OZIQ_OVQAT, rnd, with_color=False)
        kiyim_products = self._products(kiyim, KIYIM, rnd, with_color=True)

        customers = [
            Customer.objects.get_or_create(
                company=company, full_name=name,
                defaults={
                    "phone": phone,
                    "discount_percent": Decimal(discount),
                    "debt_limit": Decimal(limit),
                },
            )[0]
            for name, phone, discount, limit in MIJOZLAR
        ]

        sales = self._sales(
            rnd, options["kunlar"],
            [(oziq, oziq_products, [direktor, kassir]), (kiyim, kiyim_products, [direktor, sotuvchi])],
            customers,
        )

        self.stdout.write(self.style.SUCCESS(
            f"Tayyor: {len(oziq_products) + len(kiyim_products)} ta mahsulot, "
            f"{len(customers)} ta mijoz, {sales} ta chek."
        ))
        self.stdout.write(f"Kirish: direktor / kassir / sotuvchi — parol: {PAROL}")

    # --- Yordamchi metodlar ------------------------------------------------

    def _wipe(self):
        company = Company.objects.filter(name="Demo Savdo").first()
        if not company:
            return
        StockMovement.objects.filter(product__branch__company=company).delete()
        SaleItem.objects.filter(sale__branch__company=company).delete()
        Sale.objects.filter(branch__company=company).delete()
        Product.objects.filter(branch__company=company).delete()
        Customer.objects.filter(company=company).delete()
        User.objects.filter(company=company, is_superuser=False).delete()
        Branch.objects.filter(company=company).delete()
        company.delete()
        self.stdout.write("Eski namunaviy ma'lumotlar o'chirildi.")

    def _branch(self, company, name, trade_type, main=False):
        branch, _ = Branch.objects.get_or_create(
            company=company, name=name,
            defaults={"trade_type": trade_type, "is_main": main,
                      "address": "Toshkent sh.", "phone": "+998 71 200 00 01"},
        )
        return branch

    def _user(self, company, username, first, last, role, branches):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"first_name": first, "last_name": last, "role": role,
                      "company": company, "phone": "+998 90 000 00 00"},
        )
        if created:
            user.set_password(PAROL)
        user.branches.set(branches)
        user.branch = branches[0]
        user.save()
        return user

    def _products(self, branch, rows, rnd, with_color):
        colors = list(Color.objects.all())
        created = []
        for name, cost, price, unit, quantity, brand in rows:
            variants = rnd.sample(colors, 2) if with_color else [None]
            for color in variants:
                product, made = Product.objects.get_or_create(
                    branch=branch, name=name, color=color,
                    defaults={
                        "unit": unit, "brand": brand, "country": "O'zbekiston",
                        "cost_price": Decimal(cost), "price": Decimal(price),
                        "quantity": Decimal(quantity), "min_quantity": Decimal(max(3, quantity // 10)),
                    },
                )
                if made:
                    StockMovement.objects.create(
                        product=product, kind=StockMovement.Kind.KIRIM,
                        quantity=product.quantity, balance_after=product.quantity,
                        note="Boshlang'ich qoldiq",
                    )
                created.append(product)
        return created

    def _sales(self, rnd, days, setups, customers):
        """Har bir kun uchun bir nechta chek yaratadi (sana orqaga surilgan)."""
        today = timezone.localdate()
        methods = [PaymentMethod.NAQD] * 5 + [PaymentMethod.KARTA] * 4 + [PaymentMethod.OTKAZMA]
        total = 0

        for offset in range(days, -1, -1):
            day = today - timedelta(days=offset)
            for branch, products, staff in setups:
                self._restock(products, day, rnd)

                count = rnd.randint(4, 10) if day.weekday() >= 5 else rnd.randint(2, 7)
                # Kun ichidagi cheklar vaqt bo'yicha tartiblanadi — raqamlar ham shunga mos tushadi.
                moments = sorted(
                    timezone.make_aware(
                        timezone.datetime.combine(day, timezone.datetime.min.time())
                    ) + timedelta(hours=rnd.randint(9, 20), minutes=rnd.randint(0, 59))
                    for _ in range(count)
                )
                for moment in moments:
                    available = [p for p in products if p.quantity > 0]
                    if not available:
                        break
                    total += self._one_sale(rnd, branch, available, staff, customers, methods, moment)
        return total

    def _restock(self, products, day, rnd):
        """Har uch kunda zaxirasi tugayotgan mahsulotlar omborga to'ldiriladi."""
        if day.toordinal() % 3:
            return
        for product in products:
            if product.quantity > product.min_quantity * 2:
                continue
            amount = Decimal(rnd.randint(20, 60))
            product.quantity += amount
            product.save(update_fields=["quantity"])
            StockMovement.objects.create(
                product=product, kind=StockMovement.Kind.KIRIM, quantity=amount,
                balance_after=product.quantity, note="Ta'minotchidan kirim",
            )

    def _one_sale(self, rnd, branch, products, staff, customers, methods, moment):
        rows = rnd.sample(products, rnd.randint(1, min(4, len(products))))
        cart = []
        for product in rows:
            quantity = Decimal(rnd.randint(1, 3))
            if product.quantity < quantity:
                continue
            cart.append((product, quantity))
        if not cart:
            return 0

        customer = rnd.choice(customers) if rnd.random() < 0.3 else None
        subtotal = sum(p.price * q for p, q in cart)
        discount = Decimal("0")
        if customer and customer.discount_percent:
            discount = (subtotal * customer.discount_percent / 100).quantize(Decimal("0.01"))
        total = subtotal - discount

        method = rnd.choice(methods)
        # Qarzga faqat chegara yetadigan mijozga yozamiz.
        if customer and rnd.random() < 0.15 and customer.debt_allows(total):
            method = PaymentMethod.QARZ

        sale = Sale.objects.create(
            branch=branch, number=self._next_number(branch), cashier=rnd.choice(staff),
            customer=customer, subtotal=subtotal, discount_amount=discount, total=total,
            vat_amount=(total * branch.company.vat_percent
                        / (100 + branch.company.vat_percent)).quantize(Decimal("0.01")),
            cost_total=sum(p.cost_price * q for p, q in cart),
            payment_method=method,
            paid_amount=Decimal("0") if method == PaymentMethod.QARZ else total,
        )
        # auto_now_add maydoniga sana faqat yozilgandan keyin qo'yiladi.
        Sale.objects.filter(pk=sale.pk).update(created_at=moment)

        for product, quantity in cart:
            SaleItem.objects.create(
                sale=sale, product=product, name=str(product), barcode=product.barcode,
                unit=product.unit, quantity=quantity, price=product.price,
                cost_price=product.cost_price,
                discount_amount=(discount * (product.price * quantity) / subtotal).quantize(Decimal("0.01"))
                if discount else Decimal("0"),
            )
            product.quantity -= quantity
            product.save(update_fields=["quantity"])
            StockMovement.objects.create(
                product=product, kind=StockMovement.Kind.SOTUV, quantity=-quantity,
                balance_after=product.quantity, sale=sale, user=sale.cashier,
                note=f"Chek №{sale.number}",
            )

        if method == PaymentMethod.QARZ:
            customer.debt += total
            customer.save(update_fields=["debt"])
        return 1

    def _next_number(self, branch):
        from django.db.models import Max
        return (Sale.objects.filter(branch=branch).aggregate(top=Max("number"))["top"] or 0) + 1
