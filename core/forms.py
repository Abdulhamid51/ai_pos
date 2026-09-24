from decimal import Decimal

from django import forms

from .models import (
    Branch,
    Color,
    Company,
    Customer,
    PaymentMethod,
    Product,
    StockMovement,
    Supplier,
    User,
    Waybill,
    WaybillItem,
)

CONTROL = {"class": "input"}

# Raqamli maydonlarning qadami. `step` ni `min` bilan kelishtirib qo'yish shart:
# brauzer yaroqli qiymatlarni `min + n × step` deb hisoblaydi, shuning uchun
# yirik qadam (masalan 1000) oraliq summalarni rad etib qo'yadi.
MONEY_STEP = "0.01"     # pul — tiyinigacha
AMOUNT_STEP = "0.001"   # miqdor — kg, litr, metr uchun kasr qiymat kerak


class BranchChoiceField(forms.ModelChoiceField):
    """Ro'yxatda kompaniya nomi takrorlanmasin — faqat filial nomi va savdo turi."""

    def label_from_instance(self, obj):
        return f"{obj.name} · {obj.get_trade_type_display()}"


class BranchMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} · {obj.get_trade_type_display()}"


class ProductBaseForm(forms.ModelForm):
    """Mahsulotning barcha rang variantlari uchun umumiy maydonlar.

    Savdo turiga tegishli maydonlar ham shu formada bo'ladi, lekin
    formada faqat tanlangan filialning turiga tegishlilari ko'rinadi.
    """

    # Savdo turiga bog'liq bo'lmagan asosiy maydonlar.
    CORE_FIELDS = ["branch", "name", "sku", "unit", "cost_price", "price", "min_quantity"]
    OPTIONAL_ZERO_FIELDS = ("cost_price", "min_quantity")

    class Meta:
        model = Product
        fields = [
            "branch", "name", "sku", "unit", "cost_price", "price", "min_quantity",
            # savdo turiga xos maydonlar
            "brand", "country", "production_date", "expiry_date", "batch_number",
            "storage_conditions", "net_weight", "size", "material", "package_quantity",
            "gender", "season", "dosage_form", "active_ingredient", "dosage",
            "prescription_required", "model_name", "serial_number", "warranty_months",
            "power", "description",
        ]
        widgets = {
            "production_date": forms.DateInput(attrs={"type": "date", **CONTROL}),
            "expiry_date": forms.DateInput(attrs={"type": "date", **CONTROL}),
            "description": forms.Textarea(attrs={"rows": 3, **CONTROL}),
            "prescription_required": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput,)):
                continue
            css = widget.attrs.get("class", "")
            if "input" not in css:
                widget.attrs["class"] = (css + " input").strip()

        branches = user.visible_branches() if user else Branch.objects.filter(is_active=True)
        self.fields["branch"] = BranchChoiceField(
            label="Filial", queryset=branches.select_related("company"),
            empty_label="Filialni tanlang", widget=forms.Select(attrs=CONTROL),
        )
        if branches.count() == 1:
            self.fields["branch"].initial = branches.first()

        for name in self.OPTIONAL_ZERO_FIELDS:
            self.fields[name].required = False
            self.fields[name].initial = None

    def clean_cost_price(self):
        return self.cleaned_data.get("cost_price") or Decimal("0")

    def clean_min_quantity(self):
        return self.cleaned_data.get("min_quantity") or Decimal("0")

    def clean(self):
        """Tanlangan savdo turiga tegishli bo'lmagan maydonlar saqlanmasin."""
        cleaned = super().clean()
        branch = cleaned.get("branch")
        if not branch:
            return cleaned

        allowed = set(branch.product_fields) | set(self.CORE_FIELDS)
        for name in self.Meta.fields:
            if name not in allowed:
                cleaned[name] = self._blank_value(name)
        return cleaned

    def _blank_value(self, name):
        """Maydon turiga mos "bo'sh" qiymat — model NOT NULL bo'lsa ham yaraydi."""
        field = self.fields[name]
        if isinstance(field, forms.BooleanField):
            return False
        return getattr(field, "empty_value", None)


def resolve_color(name, hex_value):
    """Rang lug'atdan olinadi, bo'lmasa yangisi yaratiladi."""
    name = (name or "").strip()
    if not name:
        return None

    hex_value = (hex_value or "").strip()
    color, _ = Color.objects.get_or_create(name=name, defaults={"hex": hex_value})

    # Lug'atda kod bo'lmasa — foydalanuvchi bergani bilan to'ldiramiz.
    if hex_value and not color.hex:
        color.hex = hex_value
        color.save(update_fields=["hex"])
    return color


class VariantForm(forms.Form):
    """Bitta rang qatori — shu qatordan bitta mahsulot yaratiladi."""

    color = forms.CharField(
        label="Rang", max_length=50, required=False,
        widget=forms.TextInput(attrs={"class": "input", "list": "color-options",
                                      "autocomplete": "off"}),
    )
    color_hex = forms.CharField(
        label="Rang kodi", max_length=7, required=False,
        widget=forms.TextInput(attrs={"type": "color", "class": "color-input"}),
    )
    barcode = forms.CharField(
        label="Shtrix-kod", max_length=64, required=False,
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "avtomatik"}),
    )
    quantity = forms.DecimalField(
        label="Boshlang'ich soni", max_digits=12, decimal_places=3, min_value=Decimal("0"),
        widget=forms.NumberInput(attrs={"class": "input", "step": AMOUNT_STEP}),
    )
    image = forms.ImageField(
        label="Rasm", required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/*", "class": "file-input"}),
    )

    def is_filled(self):
        cd = self.cleaned_data
        return bool(cd.get("color") or cd.get("barcode") or cd.get("image") or cd.get("quantity"))

    def get_or_create_color(self):
        return resolve_color(
            self.cleaned_data.get("color"), self.cleaned_data.get("color_hex")
        )


VariantFormSet = forms.formset_factory(VariantForm, extra=0, min_num=1, validate_min=True)


class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ["name", "trade_type", "address", "phone", "is_main", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs=CONTROL),
            "trade_type": forms.Select(attrs=CONTROL),
            "address": forms.TextInput(attrs=CONTROL),
            "phone": forms.TextInput(attrs=CONTROL),
            "is_main": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }


class CompanySettingsForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "name", "currency", "vat_percent", "barcode_prefix", "barcode_length",
            "receipt_footer", "allow_negative_stock",
        ]
        widgets = {
            "name": forms.TextInput(attrs=CONTROL),
            "currency": forms.Select(attrs=CONTROL),
            "vat_percent": forms.NumberInput(attrs={"step": "0.01", "min": "0", **CONTROL}),
            "barcode_prefix": forms.TextInput(attrs=CONTROL),
            "barcode_length": forms.NumberInput(attrs={"min": "8", "max": "20", **CONTROL}),
            "receipt_footer": forms.TextInput(attrs=CONTROL),
            "allow_negative_stock": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def clean_barcode_length(self):
        length = self.cleaned_data["barcode_length"]
        if not 8 <= length <= 20:
            raise forms.ValidationError("Uzunlik 8 va 20 orasida bo'lishi kerak.")
        return length


class StaffForm(forms.ModelForm):
    """Xodim qo'shish va tahrirlash. Bir nechta filial biriktirilishi mumkin."""

    branches = BranchMultipleChoiceField(
        label="Biriktirilgan filiallar", queryset=Branch.objects.none(), required=False,
        widget=forms.SelectMultiple(attrs={"class": "input"}),
        help_text="Bir nechta filial biriktirilsa, xodim sozlamalardan filialni almashtira oladi.",
    )
    branch = BranchChoiceField(
        label="Faol filial", queryset=Branch.objects.none(), required=False,
        widget=forms.Select(attrs=CONTROL), empty_label="Avtomatik (birinchi filial)",
        help_text="Hozir ishlayotgan filiali.",
    )

    password = forms.CharField(
        label="Parol", required=False, widget=forms.PasswordInput(attrs=CONTROL),
        help_text="Tahrirlashda bo'sh qoldirilsa, parol o'zgarmaydi.",
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "phone", "role",
                  "branches", "branch", "is_active"]
        labels = {
            "username": "Login",
            "first_name": "Ism",
            "last_name": "Familiya",
            "is_active": "Faol",
        }
        help_texts = {
            "username": "Harflar, raqamlar va @ . + - _ belgilari.",
            "is_active": "",
        }
        widgets = {
            "username": forms.TextInput(attrs=CONTROL),
            "first_name": forms.TextInput(attrs=CONTROL),
            "last_name": forms.TextInput(attrs=CONTROL),
            "phone": forms.TextInput(attrs=CONTROL),
            "role": forms.Select(attrs=CONTROL),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        branches = Branch.objects.filter(company=company) if company else Branch.objects.all()
        self.fields["branches"].queryset = branches.select_related("company")
        self.fields["branch"].queryset = branches.select_related("company")

        if self.instance.pk is None:
            self.fields["password"].required = True

    def clean(self):
        cleaned = super().clean()
        branches = cleaned.get("branches")
        active = cleaned.get("branch")
        if active and branches and active not in branches:
            self.add_error("branch", "Faol filial biriktirilgan filiallardan biri bo'lishi kerak.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.company and not user.company_id:
            user.company = self.company
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
            self.save_m2m()
            # Faol filial ko'rsatilmagan bo'lsa — birinchisini olamiz.
            if not user.branch_id:
                user.branch = user.branches.first()
                user.save(update_fields=["branch"])
        return user


class BranchSwitchForm(forms.Form):
    """Xodim o'zining faol filialini almashtiradi."""

    branch = forms.ModelChoiceField(
        label="Faol filial", queryset=Branch.objects.none(),
        widget=forms.Select(attrs=CONTROL), empty_label=None,
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["branch"].queryset = user.branches.filter(is_active=True)
        self.fields["branch"].initial = user.branch_id


class ProductEditForm(ProductBaseForm):
    """Mavjud mahsulotni tahrirlash — bitta rang, bitta shtrix-kod.

    Qoldiq bu yerda o'zgartirilmaydi: uning uchun alohida ombor formasi bor,
    shunda har bir o'zgarish jurnalga tushadi.
    """

    CORE_FIELDS = ProductBaseForm.CORE_FIELDS + ["barcode", "image", "is_active"]

    color = forms.CharField(
        label="Rang", max_length=50, required=False,
        widget=forms.TextInput(attrs={"class": "input", "list": "color-options",
                                      "autocomplete": "off"}),
    )
    color_hex = forms.CharField(
        label="Rang kodi", max_length=7, required=False,
        widget=forms.TextInput(attrs={"type": "color", "class": "color-input"}),
    )

    class Meta(ProductBaseForm.Meta):
        fields = ProductBaseForm.Meta.fields + ["barcode", "image", "is_active"]
        widgets = {
            **ProductBaseForm.Meta.widgets,
            "barcode": forms.TextInput(attrs={"placeholder": "avtomatik", **CONTROL}),
            "image": forms.ClearableFileInput(attrs={"accept": "image/*"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["barcode"].required = False
        if self.instance.pk and self.instance.color:
            self.fields["color"].initial = self.instance.color.name
            self.fields["color_hex"].initial = self.instance.color.hex

    def clean_barcode(self):
        """Filial ichida shtrix-kod takrorlanmasin."""
        barcode = (self.cleaned_data.get("barcode") or "").strip()
        if not barcode:
            return ""
        branch = self.data.get("branch") or self.instance.branch_id
        clash = Product.objects.filter(branch_id=branch, barcode=barcode).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("Bu shtrix-kod shu filialda band.")
        return barcode

    def save(self, commit=True):
        product = super().save(commit=False)
        product.color = resolve_color(
            self.cleaned_data.get("color"), self.cleaned_data.get("color_hex")
        )
        if commit:
            product.save()
        return product


class StockAdjustForm(forms.Form):
    """Qoldiqni o'zgartirish: kirim, chiqim yoki inventarizatsiya tuzatishi."""

    KINDS = [
        (StockMovement.Kind.KIRIM, "Kirim (omborga qo'shish)"),
        (StockMovement.Kind.CHIQIM, "Chiqim (yaroqsiz, yo'qolgan)"),
        (StockMovement.Kind.TUZATISH, "Tuzatish (aniq qoldiqni kiritish)"),
    ]

    kind = forms.TypedChoiceField(
        label="Amal", choices=KINDS, coerce=int, initial=StockMovement.Kind.KIRIM,
        widget=forms.Select(attrs={**CONTROL, "data-no-search": "1"}),
    )
    quantity = forms.DecimalField(
        label="Miqdor", max_digits=12, decimal_places=3, min_value=Decimal("0"),
        widget=forms.NumberInput(attrs={"step": AMOUNT_STEP, **CONTROL}),
        help_text="Tuzatishda — omborda aslida qancha borligi.",
    )
    cost_price = forms.DecimalField(
        label="Yangi tan narxi", max_digits=14, decimal_places=2, required=False,
        min_value=Decimal("0"),
        widget=forms.NumberInput(attrs={"step": MONEY_STEP, **CONTROL}),
        help_text="Faqat kirimda: bo'sh qoldirilsa eski tan narxi saqlanadi.",
    )
    note = forms.CharField(
        label="Izoh", max_length=255, required=False,
        widget=forms.TextInput(attrs={**CONTROL, "placeholder": "masalan: ta'minotchidan keldi"}),
    )

    def delta_for(self, product):
        """Mahsulotning hozirgi qoldig'iga nisbatan o'zgarish miqdori."""
        kind = self.cleaned_data["kind"]
        quantity = self.cleaned_data["quantity"]
        if kind == StockMovement.Kind.KIRIM:
            return quantity
        if kind == StockMovement.Kind.CHIQIM:
            return -quantity
        return quantity - product.quantity      # tuzatish


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            "full_name", "phone", "address", "discount_percent",
            "debt_limit", "note", "is_active",
        ]
        widgets = {
            "full_name": forms.TextInput(attrs=CONTROL),
            "phone": forms.TextInput(attrs={"placeholder": "+998 90 123 45 67", **CONTROL}),
            "address": forms.TextInput(attrs=CONTROL),
            "discount_percent": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100", **CONTROL}),
            "debt_limit": forms.NumberInput(attrs={"step": MONEY_STEP, "min": "0", **CONTROL}),
            "note": forms.TextInput(attrs=CONTROL),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def clean_discount_percent(self):
        value = self.cleaned_data.get("discount_percent") or Decimal("0")
        if value > 100:
            raise forms.ValidationError("Chegirma 100 foizdan oshmasligi kerak.")
        return value


class CustomerPaymentForm(forms.Form):
    """Mijoz qarzini to'laydi."""

    amount = forms.DecimalField(
        label="To'lov summasi", max_digits=14, decimal_places=2, min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": MONEY_STEP, **CONTROL}),
    )
    note = forms.CharField(
        label="Izoh", max_length=255, required=False, widget=forms.TextInput(attrs=CONTROL)
    )

    def __init__(self, *args, customer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.customer = customer
        if customer:
            self.fields["amount"].initial = customer.debt

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if self.customer and amount > self.customer.debt:
            raise forms.ValidationError(
                f"Qarz {self.customer.debt:.0f} — bundan ortiq to'lov qabul qilinmaydi."
            )
        return amount


# ---------------------------------------------------------------------------
# Ta'minotchilar va nakladnoylar
# ---------------------------------------------------------------------------

class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "phone", "tin", "address", "note", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs=CONTROL),
            "phone": forms.TextInput(attrs={"placeholder": "+998 90 123 45 67", **CONTROL}),
            "tin": forms.TextInput(attrs=CONTROL),
            "address": forms.TextInput(attrs=CONTROL),
            "note": forms.TextInput(attrs=CONTROL),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company

    def clean_name(self):
        name = " ".join((self.cleaned_data.get("name") or "").split())
        duplicate = Supplier.objects.filter(company=self.company, name__iexact=name)
        if self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError("Bu nomdagi ta'minotchi allaqachon bor.")
        return name


class WaybillUploadForm(forms.Form):
    """Nakladnoy faylini yuklash."""

    kind = forms.TypedChoiceField(
        label="Turi", choices=Waybill.Kind.choices, coerce=int,
        initial=Waybill.Kind.QABUL, widget=forms.Select(attrs=CONTROL),
    )
    file = forms.FileField(
        label="Fayl",
        help_text="Rasm (jpg, png, webp), PDF, Excel (xlsx) yoki CSV — 10 MB gacha.",
        widget=forms.ClearableFileInput(attrs={
            "class": "input", "accept": ".jpg,.jpeg,.png,.webp,.pdf,.xlsx,.csv",
        }),
    )


class WaybillHeaderForm(forms.ModelForm):
    """Nakladnoy sarlavhasi: kimdan/kimga, raqam, sana."""

    class Meta:
        model = Waybill
        fields = ["supplier", "customer", "number", "doc_date", "on_credit", "note"]
        widgets = {
            "supplier": forms.Select(attrs=CONTROL),
            "customer": forms.Select(attrs=CONTROL),
            "number": forms.TextInput(attrs=CONTROL),
            "doc_date": forms.DateInput(attrs={"type": "date", **CONTROL}, format="%Y-%m-%d"),
            "on_credit": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "note": forms.TextInput(attrs=CONTROL),
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.objects.filter(company=company, is_active=True)
        self.fields["customer"].queryset = Customer.objects.filter(company=company, is_active=True)
        self.fields["supplier"].empty_label = "— tanlanmagan —"
        self.fields["customer"].empty_label = "— tanlanmagan —"

        # Qabulda mijoz, sotuvda ta'minotchi va "qarzga" belgisi kerak emas.
        if self.instance.kind == Waybill.Kind.QABUL:
            del self.fields["customer"]
        else:
            del self.fields["supplier"]
            del self.fields["on_credit"]


class ProductChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj} · {obj.barcode}" if obj.barcode else str(obj)


class WaybillItemForm(forms.ModelForm):
    """Nakladnoy qatori — AI o'qigan qiymatlarni foydalanuvchi tuzatadi."""

    product = ProductChoiceField(
        label="Mahsulot", queryset=Product.objects.none(), required=False,
        empty_label="— yangi mahsulot —", widget=forms.Select(attrs=CONTROL),
    )

    class Meta:
        model = WaybillItem
        fields = ["product", "name", "barcode", "unit", "quantity", "price", "sale_price"]
        widgets = {
            "name": forms.TextInput(attrs=CONTROL),
            "barcode": forms.TextInput(attrs=CONTROL),
            "unit": forms.TextInput(attrs=CONTROL),
            "quantity": forms.NumberInput(attrs={"step": AMOUNT_STEP, "min": "0", **CONTROL}),
            "price": forms.NumberInput(attrs={"step": MONEY_STEP, "min": "0", **CONTROL}),
            "sale_price": forms.NumberInput(attrs={"step": MONEY_STEP, "min": "0", **CONTROL}),
        }

    def __init__(self, *args, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = (
            Product.objects.filter(branch=branch, is_active=True).select_related("color")
        )

    def save(self, commit=True):
        item = super().save(commit=False)
        # Foydalanuvchi mahsulotni o'zi almashtirgan bo'lsa — bu endi qo'lda moslash.
        if "product" in self.changed_data:
            item.matched_by = WaybillItem.Match.QOLDA if item.product else WaybillItem.Match.YOQ
            item.match_score = None
        if commit:
            item.save()
        return item


WaybillItemFormSet = forms.inlineformset_factory(
    Waybill, WaybillItem, form=WaybillItemForm, extra=0, can_delete=True,
)


class WaybillSaleForm(forms.Form):
    """Sotuv nakladnoyini tasdiqlashda to'lov turi."""

    payment_method = forms.TypedChoiceField(
        label="To'lov turi", choices=PaymentMethod.choices, coerce=int,
        initial=PaymentMethod.OTKAZMA, widget=forms.Select(attrs=CONTROL),
    )
