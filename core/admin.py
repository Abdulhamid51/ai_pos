from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    Branch,
    Color,
    Company,
    Customer,
    CustomerPayment,
    Product,
    Sale,
    SaleItem,
    StockMovement,
    User,
)


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


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "company", "discount_percent", "debt", "is_active")
    list_filter = ("company", "is_active")
    search_fields = ("full_name", "phone", "address")
    autocomplete_fields = ("company",)
    readonly_fields = ("debt",)      # qarz faqat sotuv va to'lov orqali o'zgaradi


@admin.register(CustomerPayment)
class CustomerPaymentAdmin(admin.ModelAdmin):
    list_display = ("customer", "amount", "branch", "user", "created_at")
    list_filter = ("branch", "created_at")
    search_fields = ("customer__full_name", "customer__phone", "note")
    autocomplete_fields = ("customer", "branch", "user")
    date_hierarchy = "created_at"


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    fields = ("name", "barcode", "quantity", "price", "cost_price",
              "discount_amount", "returned_quantity")
    readonly_fields = ("name", "barcode", "price", "cost_price")


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("number", "branch", "created_at", "cashier", "customer",
                    "payment_method", "total", "status")
    list_filter = ("branch", "payment_method", "status", "created_at")
    search_fields = ("number", "customer__full_name", "customer__phone", "items__name")
    autocomplete_fields = ("branch", "cashier", "customer")
    date_hierarchy = "created_at"
    inlines = [SaleItemInline]
    readonly_fields = ("number", "subtotal", "total", "vat_amount", "cost_total",
                       "paid_amount", "change_amount", "created_at")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("product", "kind", "quantity", "balance_after", "user", "created_at")
    list_filter = ("kind", "created_at", "product__branch")
    search_fields = ("product__name", "product__barcode", "note")
    autocomplete_fields = ("product", "sale", "user")
    date_hierarchy = "created_at"


admin.site.site_header = "AI POS boshqaruvi"
admin.site.site_title = "AI POS"
admin.site.index_title = "Boshqaruv paneli"
