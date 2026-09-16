from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Branch, Color, Company, Product, User


class BranchInline(admin.TabularInline):
    model = Branch
    extra = 0
    fields = ("name", "trade_type", "address", "phone", "is_main", "is_active")


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "currency", "vat_percent", "is_active")
    list_filter = ("is_active", "currency")
    search_fields = ("name", "legal_name", "tin", "phone")
    inlines = [BranchInline]
    fieldsets = (
        ("Asosiy", {"fields": ("name", "legal_name", "tin", "is_active")}),
        ("Aloqa", {"fields": ("phone", "email", "address")}),
        (
            "Sozlamalar",
            {"fields": ("currency", "vat_percent", "receipt_footer", "allow_negative_stock",
                        "barcode_prefix", "barcode_length")},
        ),
    )


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "trade_type", "phone", "is_main", "is_active")
    list_filter = ("company", "trade_type", "is_active", "is_main")
    search_fields = ("name", "address", "phone")
    autocomplete_fields = ("company",)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "get_full_name", "role", "company", "branch", "phone", "is_active")
    list_filter = ("role", "company", "branch", "is_active", "is_staff")
    search_fields = ("username", "first_name", "last_name", "phone")
    autocomplete_fields = ("company", "branch")
    filter_horizontal = ("branches",)
    fieldsets = BaseUserAdmin.fieldsets + (
        ("POS ma'lumotlari", {"fields": ("role", "company", "branches", "branch", "phone")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("POS ma'lumotlari", {"fields": ("role", "company", "branch", "phone")}),
    )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "branch", "color", "barcode", "price", "quantity", "unit", "is_active")
    list_filter = ("branch", "is_active", "unit", "color")
    search_fields = ("name", "barcode", "sku", "brand")
    autocomplete_fields = ("branch", "color")
    list_editable = ("price", "quantity")
    fieldsets = (
        ("Asosiy", {"fields": ("branch", "name", "barcode", "sku", "unit", "color", "image", "is_active")}),
        ("Narx", {"fields": ("cost_price", "price")}),
        ("Ombor", {"fields": ("quantity", "min_quantity")}),
        ("Qo'shimcha", {
            "classes": ("collapse",),
            "fields": (
                "brand", "country", "production_date", "expiry_date", "batch_number",
                "storage_conditions", "net_weight", "size", "material", "package_quantity",
                "gender", "season", "dosage_form", "active_ingredient", "dosage",
                "prescription_required", "model_name", "serial_number", "warranty_months",
                "power", "description",
            ),
        }),
    )


@admin.register(Color)
class ColorAdmin(admin.ModelAdmin):
    list_display = ("name", "hex")
    search_fields = ("name",)


admin.site.site_header = "AI POS boshqaruvi"
admin.site.site_title = "AI POS"
admin.site.index_title = "Boshqaruv paneli"
