from decimal import Decimal

from django import forms

from .models import Branch, Color, Company, Product, User

CONTROL = {"class": "input"}


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
                cleaned[name] = self.fields[name].empty_value if hasattr(
                    self.fields[name], "empty_value"
                ) else None
        return cleaned


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
        widget=forms.NumberInput(attrs={"class": "input", "step": "1", "min": "0"}),
    )
    image = forms.ImageField(
        label="Rasm", required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/*", "class": "file-input"}),
    )

    def is_filled(self):
        cd = self.cleaned_data
        return bool(cd.get("color") or cd.get("barcode") or cd.get("image") or cd.get("quantity"))

    def get_or_create_color(self):
        """Rang lug'atdan olinadi, bo'lmasa yangisi yaratiladi."""
        name = (self.cleaned_data.get("color") or "").strip()
        if not name:
            return None

        hex_value = (self.cleaned_data.get("color_hex") or "").strip()
        color, _ = Color.objects.get_or_create(name=name, defaults={"hex": hex_value})

        # Lug'atda kod bo'lmasa — foydalanuvchi bergani bilan to'ldiramiz.
        if hex_value and not color.hex:
            color.hex = hex_value
            color.save(update_fields=["hex"])
        return color


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
