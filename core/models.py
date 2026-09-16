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
