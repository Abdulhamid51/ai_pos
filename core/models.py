import random
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.db import models

from .imaging import compress_image


class TradeType(models.IntegerChoices):
    """Savdo turi — filialda nima sotilishini bildiradi.

    Mahsulot kartochkasida qaysi maydonlar ko'rinishi va interfeysning
    urg'u rangi shu turga bog'liq.
    """

    UNIVERSAL = 1, "Universal"
    OZIQ_OVQAT = 2, "Oziq-ovqat"
    QURILISH = 3, "Qurilish mollari"
    KIYIM = 4, "Kiyim-kechak va poyabzal"
    FARMASEVTIKA = 5, "Farmasevtika"
    TEXNIKA = 6, "Texnika"


# Har bir savdo turida mahsulot formasida ko'rinadigan qo'shimcha maydonlar.
# Barcha turlar uchun umumiy maydonlar (nomi, narxi, qoldiq, rang) bu ro'yxatga kirmaydi.
TRADE_TYPE_FIELDS = {
    TradeType.UNIVERSAL: ["brand", "country", "description"],
    TradeType.OZIQ_OVQAT: [
        "brand", "country", "production_date", "expiry_date",
        "batch_number", "storage_conditions", "net_weight", "description",
    ],
    TradeType.QURILISH: [
        "brand", "country", "material", "size", "net_weight",
        "package_quantity", "description",
    ],
    TradeType.KIYIM: [
        "brand", "country", "size", "material", "gender", "season", "description",
    ],
    TradeType.FARMASEVTIKA: [
        "brand", "country", "dosage_form", "active_ingredient", "dosage",
        "prescription_required", "batch_number", "production_date",
        "expiry_date", "storage_conditions", "description",
    ],
    TradeType.TEXNIKA: [
        "brand", "country", "model_name", "serial_number",
        "warranty_months", "power", "description",
    ],
}


class Company(models.Model):
    """Tizimdan foydalanadigan tashkilot va uning sozlamalari."""

    class Currency(models.TextChoices):
        UZS = "UZS", "so'm"
        USD = "USD", "dollar"

    name = models.CharField("Nomi", max_length=150, unique=True)
    legal_name = models.CharField("Yuridik nomi", max_length=200, blank=True)
    tin = models.CharField("STIR (INN)", max_length=20, blank=True)
    phone = models.CharField("Telefon", max_length=20, blank=True)
    email = models.EmailField("Email", blank=True)
    address = models.CharField("Manzil", max_length=255, blank=True)

    # --- Sozlamalar ---
    currency = models.CharField(
        "Valyuta", max_length=3, choices=Currency.choices, default=Currency.UZS
    )
    vat_percent = models.DecimalField(
        "QQS foizi", max_digits=5, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="0 bo'lsa QQS hisoblanmaydi.",
    )
    receipt_footer = models.CharField(
        "Chek pastidagi matn", max_length=255, blank=True, default="Xaridingiz uchun rahmat!"
    )
    allow_negative_stock = models.BooleanField(
        "Manfiy qoldiqqa ruxsat", default=False,
        help_text="Qoldiq yetmasa ham sotishga ruxsat berish.",
    )

    # --- Shtrix-kod generatsiyasi ---
    barcode_prefix = models.CharField(
        "Shtrix-kod prefiksi", max_length=10, blank=True,
        help_text="Avtomatik yaratilgan kod shu bilan boshlanadi. Masalan: 200",
    )
    barcode_length = models.PositiveSmallIntegerField(
        "Shtrix-kod uzunligi", default=13,
        help_text="Prefiks bilan birga umumiy belgilar soni (8-20).",
    )

    is_active = models.BooleanField("Faol", default=True)
    created_at = models.DateTimeField("Yaratilgan", auto_now_add=True)
    updated_at = models.DateTimeField("Yangilangan", auto_now=True)

    class Meta:
        verbose_name = "Kompaniya"
        verbose_name_plural = "Kompaniyalar"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Branch(models.Model):
    """Kompaniyaning filiali (do'koni). Bitta kompaniyada bir nechta bo'lishi mumkin."""

    company = models.ForeignKey(
        Company, verbose_name="Kompaniya", on_delete=models.CASCADE, related_name="branches"
    )
    name = models.CharField("Nomi", max_length=150)
    trade_type = models.PositiveSmallIntegerField(
        "Savdo turi", choices=TradeType.choices, default=TradeType.UNIVERSAL,
        help_text="Mahsulot maydonlari va interfeys rangi shunga qarab moslashadi.",
    )
    address = models.CharField("Manzil", max_length=255, blank=True)
    phone = models.CharField("Telefon", max_length=20, blank=True)
    is_main = models.BooleanField("Asosiy filial", default=False)
    is_active = models.BooleanField("Faol", default=True)
    created_at = models.DateTimeField("Yaratilgan", auto_now_add=True)

    class Meta:
        verbose_name = "Filial"
        verbose_name_plural = "Filiallar"
        ordering = ["company__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"], name="unique_branch_name_per_company"
            )
        ]

    def __str__(self):
        return f"{self.company.name} — {self.name}"

    @property
    def product_fields(self):
        """Shu filial uchun mahsulot formasida ko'rinadigan qo'shimcha maydonlar."""
        return TRADE_TYPE_FIELDS.get(self.trade_type, TRADE_TYPE_FIELDS[TradeType.UNIVERSAL])


class User(AbstractUser):
    """POS foydalanuvchisi. Django'ning standart User modeli o'rniga ishlatiladi."""

    class Role(models.IntegerChoices):
        DIREKTOR = 1, "Direktor"
        KASSIR = 2, "Kassir"
        SOTUVCHI = 3, "Sotuvchi"
        AGENT = 4, "Agent"
        TAMINOTCHI = 5, "Ta'minotchi"
        MIJOZ = 6, "Mijoz"

    role = models.PositiveSmallIntegerField(
        "Lavozim", choices=Role.choices, default=Role.SOTUVCHI
    )
    company = models.ForeignKey(
        Company, verbose_name="Kompaniya", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="employees",
    )
    branches = models.ManyToManyField(
        Branch, verbose_name="Biriktirilgan filiallar", blank=True, related_name="staff",
        help_text="Bir nechta filial biriktirilsa, xodim sozlamalardan filialni almashtira oladi.",
    )
    branch = models.ForeignKey(
        Branch, verbose_name="Faol filial", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="active_staff",
        help_text="Hozir ishlayotgan filiali. Biriktirilgan filiallardan biri bo'ladi.",
    )
    phone = models.CharField("Telefon", max_length=20, blank=True)

    class Meta:
        verbose_name = "Foydalanuvchi"
        verbose_name_plural = "Foydalanuvchilar"
        ordering = ["username"]

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def is_direktor(self):
        return self.role == self.Role.DIREKTOR

    @property
    def is_kassir(self):
        return self.role == self.Role.KASSIR

    @property
    def is_mijoz(self):
        return self.role == self.Role.MIJOZ

    @property
    def is_xodim(self):
        """Mijozdan boshqa barcha rollar — kompaniya xodimi."""
        return self.role != self.Role.MIJOZ

    def can_sell(self):
        return self.role in {
            self.Role.DIREKTOR, self.Role.KASSIR, self.Role.SOTUVCHI, self.Role.AGENT
        }

    def can_manage(self):
        """Filial va xodimlarni boshqarish huquqi — faqat direktor."""
        return self.is_direktor or self.is_superuser

    @property
    def can_switch_branch(self):
        """Filial tanlash faqat ikki va undan ortiq filial biriktirilganda ko'rinadi."""
        return self.branches.count() > 1

    def visible_branches(self):
        """Foydalanuvchi ma'lumotlarini ko'ra oladigan filiallar."""
        if self.can_manage():
            qs = Branch.objects.filter(is_active=True)
            return qs.filter(company=self.company) if self.company_id else qs
        return self.branches.filter(is_active=True)

    @property
    def active_branch(self):
        """Hozirgi filial: tanlangani, bo'lmasa biriktirilganlaridan birinchisi."""
        if self.branch_id:
            return self.branch
        return self.branches.filter(is_active=True).first()

    @property
    def trade_type(self):
        branch = self.active_branch
        return branch.trade_type if branch else TradeType.UNIVERSAL


class Color(models.Model):
    """Ranglar lug'ati — mahsulot qo'shishda takliflar shu yerdan olinadi."""

    name = models.CharField("Nomi", max_length=50, unique=True)
    hex = models.CharField("Rang kodi", max_length=7, blank=True)
    created_at = models.DateTimeField("Yaratilgan", auto_now_add=True)

    class Meta:
        verbose_name = "Rang"
        verbose_name_plural = "Ranglar"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    """Filialdagi mahsulot: narxi va qoldig'i har bir filialda alohida yuritiladi."""

    class Unit(models.TextChoices):
        DONA = "dona", "dona"
        KG = "kg", "kg"
        LITR = "litr", "litr"
        METR = "metr", "metr"
        QUTI = "quti", "quti"

    class Gender(models.TextChoices):
        ERKAK = "erkak", "Erkaklar"
        AYOL = "ayol", "Ayollar"
        BOLA = "bola", "Bolalar"
        UNISEX = "unisex", "Uniseks"

    class Season(models.TextChoices):
        YOZ = "yoz", "Yozgi"
        QISH = "qish", "Qishki"
        DEMI = "demi", "Demisezon"
        UNIVERSAL = "universal", "Mavsumsiz"

    branch = models.ForeignKey(
        Branch, verbose_name="Filial", on_delete=models.CASCADE, related_name="products"
    )
    name = models.CharField("Nomi", max_length=200)
    barcode = models.CharField("Shtrix-kod", max_length=64, blank=True, db_index=True)
    sku = models.CharField("Artikul", max_length=64, blank=True)
    unit = models.CharField("O'lchov birligi", max_length=10, choices=Unit.choices, default=Unit.DONA)

    color = models.ForeignKey(
        Color, verbose_name="Rang", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="products",
    )
    image = models.ImageField("Rasm", upload_to="products/%Y/%m/", blank=True)

    cost_price = models.DecimalField(
        "Tan narxi", max_digits=14, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    price = models.DecimalField(
        "Sotuv narxi", max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    quantity = models.DecimalField("Qoldiq", max_digits=12, decimal_places=3, default=Decimal("0"))
    min_quantity = models.DecimalField(
        "Minimal qoldiq", max_digits=12, decimal_places=3, default=Decimal("0"),
        help_text="Qoldiq shu miqdordan pasaysa, ogohlantirish beriladi.",
    )

    # --- Savdo turiga qarab ko'rinadigan maydonlar (barchasi ixtiyoriy) ---

    brand = models.CharField("Brend", max_length=120, blank=True)
    country = models.CharField("Ishlab chiqarilgan davlat", max_length=80, blank=True)
    description = models.TextField("Tavsif", blank=True)

    # Sana va partiya — oziq-ovqat, farmasevtika
    production_date = models.DateField("Ishlab chiqarilgan sana", null=True, blank=True)
    expiry_date = models.DateField("Yaroqlilik muddati", null=True, blank=True)
    batch_number = models.CharField("Partiya / seriya", max_length=64, blank=True)
    storage_conditions = models.CharField("Saqlash sharti", max_length=150, blank=True)
    net_weight = models.DecimalField(
        "Netto og'irlik (kg)", max_digits=10, decimal_places=3, null=True, blank=True
    )

    # O'lcham va material — kiyim, qurilish
    size = models.CharField("O'lcham", max_length=50, blank=True)
    material = models.CharField("Material", max_length=120, blank=True)
    package_quantity = models.DecimalField(
        "Qadoqdagi soni", max_digits=10, decimal_places=3, null=True, blank=True
    )
    gender = models.CharField("Jinsi", max_length=10, choices=Gender.choices, blank=True)
    season = models.CharField("Mavsum", max_length=10, choices=Season.choices, blank=True)

    # Farmasevtika
    dosage_form = models.CharField("Dori shakli", max_length=80, blank=True)
    active_ingredient = models.CharField("Ta'sir etuvchi modda", max_length=150, blank=True)
    dosage = models.CharField("Dozasi", max_length=80, blank=True)
    prescription_required = models.BooleanField("Retsept bo'yicha", default=False)

    # Texnika
    model_name = models.CharField("Model", max_length=120, blank=True)
    serial_number = models.CharField("Seriya raqami", max_length=120, blank=True)
    warranty_months = models.PositiveSmallIntegerField("Kafolat (oy)", null=True, blank=True)
    power = models.CharField("Quvvat / texnik ko'rsatkich", max_length=80, blank=True)

    is_active = models.BooleanField("Faol", default=True)
    created_at = models.DateTimeField("Yaratilgan", auto_now_add=True)
    updated_at = models.DateTimeField("Yangilangan", auto_now=True)

    class Meta:
        verbose_name = "Mahsulot"
        verbose_name_plural = "Mahsulotlar"
        ordering = ["name", "color__name"]
        constraints = [
            # Bitta filial ichida shtrix-kod takrorlanmasin (bo'sh qiymatlar bundan mustasno).
            models.UniqueConstraint(
                fields=["branch", "barcode"],
                condition=~models.Q(barcode=""),
                name="unique_barcode_per_branch",
            )
        ]

    def save(self, *args, **kwargs):
        # `_committed is False` — rasm hozir yuklandi, hali diskka yozilmagan.
        if self.image and not self.image._committed:
            result = compress_image(self.image)
            if result:
                content, filename = result
                self.image.save(filename, content, save=False)

        if not self.barcode and self.branch_id:
            self.barcode = generate_barcode(self.branch)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.color})" if self.color_id else self.name

    @property
    def profit(self):
        return self.price - self.cost_price

    @property
    def is_low_stock(self):
        return self.quantity <= self.min_quantity

    @property
    def is_expired(self):
        if not self.expiry_date:
            return False
        from django.utils import timezone
        return self.expiry_date < timezone.localdate()

    def extra_fields(self):
        """Savdo turiga tegishli, to'ldirilgan maydonlar — kartochkada ko'rsatish uchun."""
        rows = []
        for name in self.branch.product_fields:
            if name == "description":
                continue
            value = getattr(self, name)
            if value in (None, "", False):
                continue
            field = self._meta.get_field(name)
            if field.choices:
                value = getattr(self, f"get_{name}_display")()
            rows.append((field.verbose_name, value))
        return rows


def generate_barcode(branch):
    """Kompaniya sozlamalari asosida band bo'lmagan shtrix-kod yaratadi."""
    company = branch.company
    prefix = (company.barcode_prefix or "").strip()
    length = max(8, min(company.barcode_length or 13, 20))
    digits = max(1, length - len(prefix))

    taken = set(
        Product.objects.filter(branch=branch)
        .exclude(barcode="")
        .values_list("barcode", flat=True)
    )
    for _ in range(50):
        candidate = prefix + "".join(str(random.randint(0, 9)) for _ in range(digits))
        if candidate not in taken:
            return candidate
    # Juda kam ehtimol: bo'sh kod qaytaramiz, foydalanuvchi o'zi kiritadi.
    return ""


# ---------------------------------------------------------------------------
# Mijozlar
# ---------------------------------------------------------------------------

class Customer(models.Model):
    """Doimiy xaridor: chegirmasi va qarzi yuritiladi."""

    company = models.ForeignKey(
        Company, verbose_name="Kompaniya", on_delete=models.CASCADE, related_name="customers"
    )
    full_name = models.CharField("F.I.Sh.", max_length=150)
    phone = models.CharField("Telefon", max_length=20, blank=True, db_index=True)
    address = models.CharField("Manzil", max_length=255, blank=True)
    note = models.CharField("Izoh", max_length=255, blank=True)

    discount_percent = models.DecimalField(
        "Doimiy chegirma (%)", max_digits=5, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Kassada avtomatik qo'llanadi.",
    )
    debt = models.DecimalField(
        "Qarz", max_digits=14, decimal_places=2, default=Decimal("0"),
        help_text="Qarzga olingan tovarlar summasi. To'lov qilinganda kamayadi.",
    )
    debt_limit = models.DecimalField(
        "Qarz chegarasi", max_digits=14, decimal_places=2, default=Decimal("0"),
        help_text="0 bo'lsa cheklov yo'q.",
    )

    is_active = models.BooleanField("Faol", default=True)
    created_at = models.DateTimeField("Yaratilgan", auto_now_add=True)
    updated_at = models.DateTimeField("Yangilangan", auto_now=True)

    class Meta:
        verbose_name = "Mijoz"
        verbose_name_plural = "Mijozlar"
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} · {self.phone}" if self.phone else self.full_name

    @property
    def has_debt(self):
        return self.debt > 0

    def debt_allows(self, amount):
        """Shu summani qarzga olishga ruxsat bormi?"""
        if not self.debt_limit:
            return True
        return self.debt + amount <= self.debt_limit


class CustomerPayment(models.Model):
    """Mijozning qarz uchun to'lovi."""

    customer = models.ForeignKey(
        Customer, verbose_name="Mijoz", on_delete=models.CASCADE, related_name="payments"
    )
    branch = models.ForeignKey(
        Branch, verbose_name="Filial", on_delete=models.SET_NULL, null=True,
        related_name="customer_payments",
    )
    user = models.ForeignKey(
        "User", verbose_name="Qabul qilgan xodim", on_delete=models.SET_NULL, null=True,
        related_name="accepted_payments",
    )
    amount = models.DecimalField(
        "Summa", max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_at = models.DateTimeField("Sana", auto_now_add=True)

    class Meta:
        verbose_name = "Qarz to'lovi"
        verbose_name_plural = "Qarz to'lovlari"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer.full_name} — {self.amount}"


# ---------------------------------------------------------------------------
# Savdo
# ---------------------------------------------------------------------------

class PaymentMethod(models.IntegerChoices):
    NAQD = 1, "Naqd"
    KARTA = 2, "Plastik karta"
    OTKAZMA = 3, "O'tkazma"
    QARZ = 4, "Qarzga"


class Sale(models.Model):
    """Kassadan o'tgan bitta chek."""

    class Status(models.IntegerChoices):
        TOLANGAN = 1, "To'langan"
        QISMAN_QAYTARILGAN = 2, "Qisman qaytarilgan"
        QAYTARILGAN = 3, "Qaytarilgan"

    branch = models.ForeignKey(
        Branch, verbose_name="Filial", on_delete=models.PROTECT, related_name="sales"
    )
    number = models.PositiveIntegerField("Chek raqami")
    cashier = models.ForeignKey(
        "User", verbose_name="Kassir", on_delete=models.SET_NULL, null=True, related_name="sales"
    )
    customer = models.ForeignKey(
        Customer, verbose_name="Mijoz", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="sales",
    )

    subtotal = models.DecimalField("Chegirmasiz summa", max_digits=14, decimal_places=2, default=Decimal("0"))
    discount_amount = models.DecimalField("Chegirma", max_digits=14, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField("Jami", max_digits=14, decimal_places=2, default=Decimal("0"))
    vat_amount = models.DecimalField(
        "QQS", max_digits=14, decimal_places=2, default=Decimal("0"),
        help_text="Jami summa ichidagi QQS ulushi.",
    )
    cost_total = models.DecimalField(
        "Tan narxi jami", max_digits=14, decimal_places=2, default=Decimal("0"),
        help_text="Foydani hisoblash uchun sotuv paytidagi tan narxlari yig'indisi.",
    )

    payment_method = models.PositiveSmallIntegerField(
        "To'lov turi", choices=PaymentMethod.choices, default=PaymentMethod.NAQD
    )
    paid_amount = models.DecimalField("Berilgan pul", max_digits=14, decimal_places=2, default=Decimal("0"))
    change_amount = models.DecimalField("Qaytim", max_digits=14, decimal_places=2, default=Decimal("0"))
    refunded_amount = models.DecimalField("Qaytarilgan summa", max_digits=14, decimal_places=2, default=Decimal("0"))

    status = models.PositiveSmallIntegerField("Holat", choices=Status.choices, default=Status.TOLANGAN)
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_at = models.DateTimeField("Sana", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Sotuv"
        verbose_name_plural = "Sotuvlar"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "number"], name="unique_sale_number_per_branch")
        ]

    def __str__(self):
        return f"№{self.number} — {self.branch.name}"

    @property
    def profit(self):
        """Sof foyda: qaytarilgan qismdan keyingi summadan tan narxi ayiriladi."""
        return self.net_total - self.net_cost

    @property
    def net_total(self):
        return self.total - self.refunded_amount

    @property
    def net_cost(self):
        return sum((item.net_cost for item in self.items.all()), Decimal("0"))

    @property
    def is_debt(self):
        return self.payment_method == PaymentMethod.QARZ

    @property
    def item_count(self):
        return self.items.count()

    def recalc_status(self):
        """Qaytarilgan miqdorlarga qarab holatni yangilaydi."""
        items = list(self.items.all())
        returned = sum(item.returned_quantity for item in items)
        if not returned:
            self.status = self.Status.TOLANGAN
        elif all(item.returned_quantity >= item.quantity for item in items):
            self.status = self.Status.QAYTARILGAN
        else:
            self.status = self.Status.QISMAN_QAYTARILGAN


class SaleItem(models.Model):
    """Chekdagi bitta qator. Mahsulot o'chirilsa ham chek o'qiladigan bo'lib qoladi."""

    sale = models.ForeignKey(Sale, verbose_name="Chek", on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        Product, verbose_name="Mahsulot", on_delete=models.SET_NULL,
        null=True, related_name="sale_items",
    )

    # Sotuv paytidagi holat — keyin mahsulot o'zgarsa ham chek o'zgarmaydi.
    name = models.CharField("Nomi", max_length=250)
    barcode = models.CharField("Shtrix-kod", max_length=64, blank=True)
    unit = models.CharField("O'lchov birligi", max_length=10, blank=True)

    quantity = models.DecimalField("Soni", max_digits=12, decimal_places=3)
    price = models.DecimalField("Narxi", max_digits=14, decimal_places=2)
    cost_price = models.DecimalField("Tan narxi", max_digits=14, decimal_places=2, default=Decimal("0"))
    discount_amount = models.DecimalField("Chegirma", max_digits=14, decimal_places=2, default=Decimal("0"))
    returned_quantity = models.DecimalField("Qaytarilgan soni", max_digits=12, decimal_places=3, default=Decimal("0"))

    class Meta:
        verbose_name = "Chek qatori"
        verbose_name_plural = "Chek qatorlari"
        ordering = ["pk"]

    def __str__(self):
        return f"{self.name} × {self.quantity}"

    @property
    def gross_total(self):
        return self.price * self.quantity

    @property
    def line_total(self):
        return self.gross_total - self.discount_amount

    @property
    def returnable_quantity(self):
        return self.quantity - self.returned_quantity

    @property
    def net_quantity(self):
        return self.quantity - self.returned_quantity

    @property
    def net_cost(self):
        return self.cost_price * self.net_quantity

    @property
    def unit_net_price(self):
        """Chegirma hisobga olingan bitta dona narxi — qaytarishda ishlatiladi."""
        if not self.quantity:
            return Decimal("0")
        return (self.line_total / self.quantity).quantize(Decimal("0.01"))


class StockMovement(models.Model):
    """Ombor harakati — qoldiq nega o'zgarganini ko'rsatadi."""

    class Kind(models.IntegerChoices):
        KIRIM = 1, "Kirim"
        SOTUV = 2, "Sotuv"
        QAYTARISH = 3, "Qaytarish"
        TUZATISH = 4, "Tuzatish"
        CHIQIM = 5, "Chiqim (yaroqsiz)"

    product = models.ForeignKey(
        Product, verbose_name="Mahsulot", on_delete=models.CASCADE, related_name="movements"
    )
    kind = models.PositiveSmallIntegerField("Turi", choices=Kind.choices)
    quantity = models.DecimalField(
        "O'zgarish", max_digits=12, decimal_places=3,
        help_text="Musbat — qoldiq ortdi, manfiy — kamaydi.",
    )
    balance_after = models.DecimalField("Keyingi qoldiq", max_digits=12, decimal_places=3, default=Decimal("0"))
    sale = models.ForeignKey(
        Sale, verbose_name="Chek", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="movements",
    )
    user = models.ForeignKey(
        "User", verbose_name="Xodim", on_delete=models.SET_NULL, null=True, related_name="movements"
    )
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_at = models.DateTimeField("Sana", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Ombor harakati"
        verbose_name_plural = "Ombor harakatlari"
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.product} {self.quantity:+}"


def apply_stock(product, delta, kind, user=None, sale=None, note=""):
    """Qoldiqni o'zgartiradi va harakatni jurnalga yozadi.

    `delta` musbat bo'lsa qoldiq ortadi, manfiy bo'lsa kamayadi.
    Mahsulot obyekti chaqiruvchida ham yangilangan holda qoladi.
    """
    product.quantity = (product.quantity or Decimal("0")) + delta
    product.save(update_fields=["quantity", "updated_at"])
    return StockMovement.objects.create(
        product=product, kind=kind, quantity=delta,
        balance_after=product.quantity, sale=sale, user=user, note=note,
    )


# ---------------------------------------------------------------------------
# Semantik qidiruv
# ---------------------------------------------------------------------------

class ProductEmbedding(models.Model):
    """Mahsulotning ma'noviy "barmoq izi" — semantik qidiruv uchun vektor.

    Vektor Gemini embedding modelidan olinadi va oddiy JSON ro'yxat bo'lib
    saqlanadi (SQLite'da alohida vektor bazasi kerak emas: bir necha ming
    mahsulotgacha Python'da hisoblash yetarli tez).

    `source_hash` — vektor olingan matnning nazorat summasi. Mahsulot nomi yoki
    tavsifi o'zgarmagan bo'lsa, API qayta chaqirilmaydi.
    """

    product = models.OneToOneField(
        Product, verbose_name="Mahsulot", on_delete=models.CASCADE, related_name="embedding"
    )
    vector = models.JSONField("Vektor", default=list)
    source_hash = models.CharField("Matn nazorat summasi", max_length=64, db_index=True)
    updated_at = models.DateTimeField("Yangilangan", auto_now=True)

    class Meta:
        verbose_name = "Mahsulot vektori"
        verbose_name_plural = "Mahsulot vektorlari"

    def __str__(self):
        return f"{self.product} · {len(self.vector)} o'lcham"
