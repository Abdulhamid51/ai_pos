import io
import json
import random
import shutil
import tempfile
from decimal import Decimal

from django.conf import settings
from django.contrib.humanize.templatetags.humanize import intcomma
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.utils import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import (
    Branch,
    Color,
    Company,
    Customer,
    PaymentMethod,
    Product,
    Sale,
    StockMovement,
    TradeType,
    User,
)
from .forms import (
    CustomerForm,
    CustomerPaymentForm,
    ProductBaseForm,
    ProductEditForm,
    StockAdjustForm,
    VariantForm,
)
from .services import CheckoutError, checkout, pay_debt, refund


class BaseDataMixin:
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="Test Savdo", barcode_prefix="200", barcode_length=13)
        cls.branch = Branch.objects.create(
            company=cls.company, name="Markaziy", is_main=True, trade_type=TradeType.KIYIM
        )
        cls.food_branch = Branch.objects.create(
            company=cls.company, name="Oziq-ovqat", trade_type=TradeType.OZIQ_OVQAT
        )

    def make_director(self, username="direktor"):
        user = User.objects.create_user(
            username=username, password="parol12345",
            role=User.Role.DIREKTOR, company=self.company,
        )
        user.branches.set([self.branch, self.food_branch])
        user.branch = self.branch
        user.save()
        return user

    def make_cashier(self, username="kassir", branches=None):
        user = User.objects.create_user(
            username=username, password="parol12345",
            role=User.Role.KASSIR, company=self.company,
        )
        user.branches.set(branches or [self.branch])
        user.branch = user.branches.first()
        user.save()
        return user


# ---------------------------------------------------------------------------
# Asosiy sahifalar va rollar
# ---------------------------------------------------------------------------

class DashboardTests(BaseDataMixin, TestCase):
    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('dashboard')}")

    def test_dashboard_opens_for_logged_in_user(self):
        self.client.force_login(self.make_cashier())
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)


class UserRoleTests(BaseDataMixin, TestCase):
    def test_default_role_is_sotuvchi(self):
        user = User.objects.create_user(username="xodim", password="parol12345")
        self.assertEqual(user.role, User.Role.SOTUVCHI)
        self.assertTrue(user.can_sell())

    def test_mijoz_is_not_xodim(self):
        mijoz = User.objects.create_user(username="mijoz", password="p12345678", role=User.Role.MIJOZ)
        self.assertFalse(mijoz.is_xodim)
        self.assertFalse(mijoz.can_sell())

    def test_only_director_can_manage(self):
        self.assertTrue(self.make_director().can_manage())
        self.assertFalse(self.make_cashier().can_manage())

    def test_branch_switch_appears_only_with_two_or_more_branches(self):
        one = self.make_cashier("bitta", branches=[self.branch])
        many = self.make_cashier("kopta", branches=[self.branch, self.food_branch])
        self.assertFalse(one.can_switch_branch)
        self.assertTrue(many.can_switch_branch)

    def test_cashier_sees_only_own_branches(self):
        cashier = self.make_cashier(branches=[self.branch])
        self.assertEqual(list(cashier.visible_branches()), [self.branch])
        self.assertEqual(set(self.make_director().visible_branches()), {self.branch, self.food_branch})

    def test_trade_type_follows_active_branch(self):
        cashier = self.make_cashier(branches=[self.food_branch])
        self.assertEqual(cashier.trade_type, TradeType.OZIQ_OVQAT)


class BranchTests(BaseDataMixin, TestCase):
    def test_branch_name_is_unique_per_company(self):
        with self.assertRaises(IntegrityError):
            Branch.objects.create(company=self.company, name="Markaziy")

    def test_product_fields_depend_on_trade_type(self):
        self.assertIn("size", self.branch.product_fields)          # kiyim
        self.assertIn("expiry_date", self.food_branch.product_fields)
        self.assertNotIn("expiry_date", self.branch.product_fields)


# ---------------------------------------------------------------------------
# Mahsulot
# ---------------------------------------------------------------------------

class ProductModelTests(BaseDataMixin, TestCase):
    def setUp(self):
        self.qora = Color.objects.create(name="Qora", hex="#151b23")
        self.product = Product.objects.create(
            branch=self.branch, name="Coca-Cola 1L", barcode="4780001234567",
            cost_price=Decimal("8000"), price=Decimal("12000"),
            quantity=Decimal("10"), min_quantity=Decimal("3"),
        )

    def test_profit_and_low_stock(self):
        self.assertEqual(self.product.profit, Decimal("4000"))
        self.assertFalse(self.product.is_low_stock)
        self.product.quantity = Decimal("2")
        self.assertTrue(self.product.is_low_stock)

    def test_barcode_is_unique_per_branch(self):
        with self.assertRaises(IntegrityError):
            Product.objects.create(
                branch=self.branch, name="Nusxa", barcode="4780001234567", price=Decimal("1000")
            )

    def test_barcode_is_generated_when_empty(self):
        product = Product.objects.create(branch=self.branch, name="Kodsiz", price=Decimal("1000"))
        self.assertTrue(product.barcode.startswith("200"))
        self.assertEqual(len(product.barcode), 13)

    def test_generated_barcodes_do_not_repeat(self):
        codes = {
            Product.objects.create(branch=self.branch, name=f"M{i}", price=Decimal("1")).barcode
            for i in range(25)
        }
        self.assertEqual(len(codes), 25)

    def test_barcode_length_and_prefix_come_from_company(self):
        self.company.barcode_prefix = "777"
        self.company.barcode_length = 10
        self.company.save()
        product = Product.objects.create(branch=self.branch, name="Qisqa", price=Decimal("1"))
        self.assertEqual(len(product.barcode), 10)
        self.assertTrue(product.barcode.startswith("777"))

    def test_extra_fields_shows_only_trade_type_data(self):
        product = Product.objects.create(
            branch=self.branch, name="Futbolka", price=Decimal("1"),
            size="XL", brand="Polo", expiry_date="2030-01-01",
        )
        labels = [str(label) for label, _ in product.extra_fields()]
        self.assertIn("O'lcham", labels)
        self.assertIn("Brend", labels)
        self.assertNotIn("Yaroqlilik muddati", labels)   # kiyim uchun bu maydon ko'rinmaydi


class ProductViewTests(BaseDataMixin, TestCase):
    def setUp(self):
        self.user = self.make_director()
        self.client.force_login(self.user)

    def _post_data(self, rows, branch=None, **base):
        data = {
            "branch": (branch or self.branch).pk,
            "name": "Futbolka Polo",
            "sku": "",
            "unit": Product.Unit.DONA,
            "cost_price": "60000",
            "price": "95000",
            "min_quantity": "2",
            "variant-TOTAL_FORMS": str(len(rows)),
            "variant-INITIAL_FORMS": "0",
            "variant-MIN_NUM_FORMS": "1",
            "variant-MAX_NUM_FORMS": "1000",
        }
        data.update(base)
        for i, row in enumerate(rows):
            for key, value in row.items():
                data[f"variant-{i}-{key}"] = value
        return data

    def test_each_color_row_creates_its_own_product(self):
        response = self.client.post(reverse("product_create"), self._post_data([
            {"color": "Qora", "color_hex": "#111111", "barcode": "1001", "quantity": "10"},
            {"color": "Oq", "color_hex": "#ffffff", "barcode": "1002", "quantity": "4"},
            {"color": "Ko'k", "color_hex": "#1f5fa9", "barcode": "", "quantity": "7"},
        ]))
        self.assertRedirects(response, reverse("product_list"))
        self.assertEqual(Product.objects.count(), 3)
        self.assertEqual(
            sorted(p.color.name for p in Product.objects.all()), sorted(["Qora", "Oq", "Ko'k"])
        )
        self.assertEqual(Product.objects.get(color__name="Qora").quantity, Decimal("10"))

    def test_colors_are_saved_to_dictionary_and_reused(self):
        self.client.post(reverse("product_create"), self._post_data(
            [{"color": "Qora", "color_hex": "#151b23", "barcode": "", "quantity": "1"}]))
        self.client.post(reverse("product_create"), self._post_data(
            [{"color": "Qora", "color_hex": "#151b23", "barcode": "", "quantity": "2"}]))

        self.assertEqual(Color.objects.filter(name="Qora").count(), 1)
        self.assertEqual(Color.objects.get(name="Qora").hex, "#151b23")

    def test_existing_color_keeps_its_hex(self):
        Color.objects.create(name="Qora", hex="#000000")
        self.client.post(reverse("product_create"), self._post_data(
            [{"color": "Qora", "color_hex": "#ff0000", "barcode": "", "quantity": "1"}]))
        self.assertEqual(Color.objects.get(name="Qora").hex, "#000000")

    def test_empty_barcode_is_generated(self):
        self.client.post(reverse("product_create"), self._post_data(
            [{"color": "Qora", "color_hex": "", "barcode": "", "quantity": "3"}]))
        product = Product.objects.get()
        self.assertEqual(len(product.barcode), 13)
        self.assertTrue(product.barcode.startswith("200"))

    def test_fields_outside_trade_type_are_ignored(self):
        # Kiyim filialiga yaroqlilik muddati yuborilsa ham saqlanmasligi kerak.
        self.client.post(reverse("product_create"), self._post_data(
            [{"color": "Qora", "color_hex": "", "barcode": "", "quantity": "1"}],
            size="XL", expiry_date="2030-05-01"))
        product = Product.objects.get()
        self.assertEqual(product.size, "XL")
        self.assertIsNone(product.expiry_date)

    def test_empty_rows_are_skipped(self):
        self.client.post(reverse("product_create"), self._post_data([
            {"color": "Qizil", "color_hex": "", "barcode": "", "quantity": "5"},
            {"color": "", "color_hex": "", "barcode": "", "quantity": ""},
        ]))
        self.assertEqual(Product.objects.count(), 1)

    def test_invalid_base_form_creates_nothing(self):
        response = self.client.post(reverse("product_create"), self._post_data(
            [{"color": "Qora", "color_hex": "", "barcode": "", "quantity": "3"}], name=""))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Product.objects.count(), 0)

    def test_cashier_sees_only_own_branch_products(self):
        Product.objects.create(branch=self.branch, name="Kiyim", price=Decimal("1"))
        Product.objects.create(branch=self.food_branch, name="Non", price=Decimal("1"))

        self.client.force_login(self.make_cashier(branches=[self.food_branch]))
        names = [p.name for p in self.client.get(reverse("product_list")).context["products"]]
        self.assertEqual(names, ["Non"])

    def test_list_renders_both_views(self):
        Product.objects.create(branch=self.branch, name="Non", price=Decimal("4000"))
        html = self.client.get(reverse("product_list")).content.decode()
        self.assertIn('data-view-body="table"', html)
        self.assertIn('data-view-body="cards"', html)


# ---------------------------------------------------------------------------
# Xodimlar va sozlamalar
# ---------------------------------------------------------------------------

class StaffAndSettingsTests(BaseDataMixin, TestCase):
    def setUp(self):
        self.director = self.make_director()
        self.cashier = self.make_cashier(branches=[self.branch])

    def test_staff_page_is_director_only(self):
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("staff_list")).status_code, 302)

        self.client.force_login(self.director)
        self.assertEqual(self.client.get(reverse("staff_list")).status_code, 200)

    def test_director_creates_staff_with_several_branches(self):
        self.client.force_login(self.director)
        response = self.client.post(reverse("staff_create"), {
            "username": "yangi", "first_name": "Yangi", "last_name": "Xodim",
            "phone": "+998901112233", "role": User.Role.SOTUVCHI,
            "branches": [self.branch.pk, self.food_branch.pk],
            "branch": self.branch.pk, "password": "parol12345", "is_active": "on",
        })
        self.assertRedirects(response, reverse("staff_list"))

        user = User.objects.get(username="yangi")
        self.assertEqual(user.branches.count(), 2)
        self.assertTrue(user.can_switch_branch)
        self.assertTrue(user.check_password("parol12345"))

    def test_active_branch_must_be_one_of_assigned(self):
        self.client.force_login(self.director)
        response = self.client.post(reverse("staff_create"), {
            "username": "xato", "role": User.Role.SOTUVCHI,
            "branches": [self.branch.pk], "branch": self.food_branch.pk,
            "password": "parol12345",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="xato").exists())

    def test_staff_switches_active_branch(self):
        user = self.make_cashier("almashtiruvchi", branches=[self.branch, self.food_branch])
        self.client.force_login(user)
        self.client.post(reverse("switch_branch"), {"branch": self.food_branch.pk, "next": "/"})

        user.refresh_from_db()
        self.assertEqual(user.branch, self.food_branch)
        self.assertEqual(user.trade_type, TradeType.OZIQ_OVQAT)

    def test_staff_cannot_switch_to_foreign_branch(self):
        user = self.make_cashier("cheklangan", branches=[self.branch])
        self.client.force_login(user)
        self.client.post(reverse("switch_branch"), {"branch": self.food_branch.pk, "next": "/"})

        user.refresh_from_db()
        self.assertEqual(user.branch, self.branch)

    def test_director_creates_branch_from_settings(self):
        self.client.force_login(self.director)
        self.client.post(reverse("branch_create"), {
            "name": "Yunusobod", "trade_type": TradeType.FARMASEVTIKA,
            "address": "Toshkent", "phone": "", "is_active": "on", "next": "/",
        })
        branch = Branch.objects.get(name="Yunusobod")
        self.assertEqual(branch.company, self.company)
        self.assertEqual(branch.trade_type, TradeType.FARMASEVTIKA)

    def test_cashier_cannot_create_branch(self):
        self.client.force_login(self.cashier)
        self.client.post(reverse("branch_create"), {
            "name": "Ruxsatsiz", "trade_type": TradeType.UNIVERSAL, "next": "/",
        })
        self.assertFalse(Branch.objects.filter(name="Ruxsatsiz").exists())

    def test_director_deactivates_branch(self):
        self.client.force_login(self.director)
        self.client.post(reverse("branch_toggle", args=[self.food_branch.pk]), {"next": "/"})

        self.food_branch.refresh_from_db()
        self.assertFalse(self.food_branch.is_active)

    def test_company_barcode_settings_are_saved(self):
        self.client.force_login(self.director)
        self.client.post(reverse("company_save"), {
            "name": self.company.name, "currency": "UZS", "vat_percent": "12",
            "barcode_prefix": "999", "barcode_length": "10",
            "receipt_footer": "Rahmat", "next": "/",
        })
        self.company.refresh_from_db()
        self.assertEqual(self.company.barcode_prefix, "999")
        self.assertEqual(self.company.barcode_length, 10)

    def test_barcode_length_is_validated(self):
        self.client.force_login(self.director)
        self.client.post(reverse("company_save"), {
            "name": self.company.name, "currency": "UZS", "vat_percent": "0",
            "barcode_prefix": "1", "barcode_length": "40", "receipt_footer": "", "next": "/",
        })
        self.company.refresh_from_db()
        self.assertEqual(self.company.barcode_length, 13)   # o'zgarmadi


class ThemeContextTests(BaseDataMixin, TestCase):
    def test_director_keeps_default_accent(self):
        self.client.force_login(self.make_director())
        self.assertEqual(self.client.get(reverse("dashboard")).context["trade_type"], TradeType.UNIVERSAL)

    def test_staff_accent_follows_trade_type(self):
        self.client.force_login(self.make_cashier(branches=[self.food_branch]))
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["trade_type"], TradeType.OZIQ_OVQAT)
        self.assertIn('data-trade="2"', response.content.decode())


# ---------------------------------------------------------------------------
# Kassa va savdo
# ---------------------------------------------------------------------------

class SalesMixin(BaseDataMixin):
    """Sotuv testlari uchun umumiy mahsulotlar va yordamchi metodlar."""

    def setUp(self):
        self.cashier = self.make_cashier(branches=[self.branch])
        self.non = Product.objects.create(
            branch=self.branch, name="Non", barcode="1001",
            cost_price=Decimal("2000"), price=Decimal("4000"), quantity=Decimal("50"),
        )
        self.sut = Product.objects.create(
            branch=self.branch, name="Sut 1L", barcode="1002",
            cost_price=Decimal("8000"), price=Decimal("12000"), quantity=Decimal("20"),
        )
        self.boshqa = Product.objects.create(
            branch=self.food_branch, name="Shakar", barcode="2001",
            cost_price=Decimal("9000"), price=Decimal("14000"), quantity=Decimal("30"),
        )

    #: `rows` berilmasa shu qator sotiladi (2 × 4000 = 8000 so'm).
    DEFAULT_ROWS = object()

    def sell(self, rows=DEFAULT_ROWS, **kwargs):
        kwargs.setdefault("user", self.cashier)
        kwargs.setdefault("branch", self.branch)
        kwargs.setdefault("payment_method", PaymentMethod.KARTA)
        if rows is self.DEFAULT_ROWS:
            rows = [{"product": self.non.pk, "quantity": 2}]
        return checkout(rows=rows, **kwargs)


class CheckoutTests(SalesMixin, TestCase):
    def test_sale_writes_items_and_reduces_stock(self):
        sale = self.sell([
            {"product": self.non.pk, "quantity": 3},
            {"product": self.sut.pk, "quantity": 2},
        ])
        self.non.refresh_from_db()
        self.sut.refresh_from_db()

        self.assertEqual(sale.items.count(), 2)
        self.assertEqual(sale.total, Decimal("36000"))          # 3×4000 + 2×12000
        self.assertEqual(sale.cost_total, Decimal("22000"))     # 3×2000 + 2×8000
        self.assertEqual(self.non.quantity, Decimal("47"))
        self.assertEqual(self.sut.quantity, Decimal("18"))

    def test_every_sale_is_logged_as_stock_movement(self):
        sale = self.sell([{"product": self.non.pk, "quantity": 5}])
        movement = StockMovement.objects.get(product=self.non, kind=StockMovement.Kind.SOTUV)
        self.assertEqual(movement.quantity, Decimal("-5"))
        self.assertEqual(movement.balance_after, Decimal("45"))
        self.assertEqual(movement.sale, sale)

    def test_check_numbers_grow_per_branch(self):
        first = self.sell()
        second = self.sell()
        other = checkout(
            user=self.make_director(), branch=self.food_branch,
            rows=[{"product": self.boshqa.pk, "quantity": 1}],
            payment_method=PaymentMethod.NAQD,
        )
        self.assertEqual([first.number, second.number, other.number], [1, 2, 1])

    def test_same_product_twice_is_merged_into_one_row(self):
        sale = self.sell([
            {"product": self.non.pk, "quantity": 1},
            {"product": self.non.pk, "quantity": 2},
        ])
        self.assertEqual(sale.items.count(), 1)
        self.assertEqual(sale.items.get().quantity, Decimal("3"))

    def test_cash_payment_returns_change(self):
        sale = self.sell(payment_method=PaymentMethod.NAQD, paid_amount="10000")
        self.assertEqual(sale.total, Decimal("8000"))
        self.assertEqual(sale.change_amount, Decimal("2000"))

    def test_cash_shortfall_is_rejected(self):
        with self.assertRaises(CheckoutError):
            self.sell(payment_method=PaymentMethod.NAQD, paid_amount="1000")
        self.assertEqual(Sale.objects.count(), 0)

    def test_stock_shortfall_is_rejected_and_nothing_is_saved(self):
        with self.assertRaises(CheckoutError):
            self.sell([{"product": self.non.pk, "quantity": 500}])

        self.non.refresh_from_db()
        self.assertEqual(self.non.quantity, Decimal("50"))
        self.assertEqual(Sale.objects.count(), 0)

    def test_negative_stock_is_allowed_when_company_permits(self):
        self.company.allow_negative_stock = True
        self.company.save(update_fields=["allow_negative_stock"])

        self.sell([{"product": self.non.pk, "quantity": 60}])
        self.non.refresh_from_db()
        self.assertEqual(self.non.quantity, Decimal("-10"))

    def test_product_of_another_branch_is_rejected(self):
        with self.assertRaises(CheckoutError):
            self.sell([{"product": self.boshqa.pk, "quantity": 1}])

    def test_empty_cart_is_rejected(self):
        with self.assertRaises(CheckoutError):
            self.sell([])

    def test_customer_without_sale_rights_cannot_sell(self):
        mijoz = User.objects.create_user(
            username="oddiy", password="parol12345", role=User.Role.MIJOZ, company=self.company
        )
        with self.assertRaises(CheckoutError):
            self.sell(user=mijoz)

    def test_discount_is_spread_across_rows(self):
        sale = self.sell(
            [{"product": self.non.pk, "quantity": 1}, {"product": self.sut.pk, "quantity": 1}],
            discount_amount=Decimal("1600"),
        )
        self.assertEqual(sale.total, Decimal("14400"))          # 16000 − 1600
        self.assertEqual(
            sum(item.discount_amount for item in sale.items.all()), Decimal("1600")
        )

    def test_customer_discount_is_applied_automatically(self):
        customer = Customer.objects.create(
            company=self.company, full_name="Aziz", discount_percent=Decimal("10")
        )
        sale = self.sell([{"product": self.sut.pk, "quantity": 1}], customer=customer)
        self.assertEqual(sale.discount_amount, Decimal("1200"))
        self.assertEqual(sale.total, Decimal("10800"))

    def test_discount_above_subtotal_is_rejected(self):
        with self.assertRaises(CheckoutError):
            self.sell([{"product": self.non.pk, "quantity": 1}], discount_amount=Decimal("99999"))

    def test_vat_is_taken_out_of_the_total(self):
        self.company.vat_percent = Decimal("12")
        self.company.save(update_fields=["vat_percent"])

        sale = self.sell([{"product": self.non.pk, "quantity": 1}])   # 4000
        self.assertEqual(sale.total, Decimal("4000"))                 # QQS narx ichida
        self.assertEqual(sale.vat_amount, Decimal("428.57"))          # 4000 × 12/112


class DebtSaleTests(SalesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.customer = Customer.objects.create(
            company=self.company, full_name="Qarzdor Mijoz", phone="+998901112233"
        )

    def test_debt_sale_increases_customer_debt(self):
        sale = self.sell(payment_method=PaymentMethod.QARZ, customer=self.customer)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.debt, sale.total)
        self.assertEqual(sale.paid_amount, Decimal("0"))

    def test_debt_sale_requires_a_customer(self):
        with self.assertRaises(CheckoutError):
            self.sell(payment_method=PaymentMethod.QARZ)

    def test_debt_limit_is_respected(self):
        self.customer.debt_limit = Decimal("5000")
        self.customer.save(update_fields=["debt_limit"])
        with self.assertRaises(CheckoutError):
            self.sell([{"product": self.sut.pk, "quantity": 1}],
                      payment_method=PaymentMethod.QARZ, customer=self.customer)

    def test_payment_reduces_debt_and_is_recorded(self):
        self.sell(payment_method=PaymentMethod.QARZ, customer=self.customer)
        pay_debt(customer=self.customer, amount=Decimal("3000"), user=self.cashier)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.debt, Decimal("5000"))
        self.assertEqual(self.customer.payments.get().amount, Decimal("3000"))

    def test_payment_above_debt_is_rejected(self):
        self.sell(payment_method=PaymentMethod.QARZ, customer=self.customer)
        with self.assertRaises(CheckoutError):
            pay_debt(customer=self.customer, amount=Decimal("100000"), user=self.cashier)


class RefundTests(SalesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.sale = self.sell([
            {"product": self.non.pk, "quantity": 4},
            {"product": self.sut.pk, "quantity": 2},
        ])
        self.non_item = self.sale.items.get(product=self.non)
        self.sut_item = self.sale.items.get(product=self.sut)

    def test_partial_refund_returns_stock_and_marks_status(self):
        amount = refund(sale=self.sale, rows={self.non_item.pk: 2}, user=self.cashier)
        self.non.refresh_from_db()
        self.sale.refresh_from_db()

        self.assertEqual(amount, Decimal("8000"))
        self.assertEqual(self.non.quantity, Decimal("48"))      # 50 − 4 + 2
        self.assertEqual(self.sale.status, Sale.Status.QISMAN_QAYTARILGAN)
        self.assertEqual(self.sale.net_total, Decimal("32000"))

    def test_full_refund_marks_sale_as_returned(self):
        refund(sale=self.sale, rows={self.non_item.pk: 4, self.sut_item.pk: 2}, user=self.cashier)
        self.sale.refresh_from_db()

        self.assertEqual(self.sale.status, Sale.Status.QAYTARILGAN)
        self.assertEqual(self.sale.refunded_amount, self.sale.total)
        self.assertEqual(self.sale.net_total, Decimal("0"))

    def test_refund_beyond_sold_quantity_is_rejected(self):
        with self.assertRaises(CheckoutError):
            refund(sale=self.sale, rows={self.non_item.pk: 10}, user=self.cashier)

        self.non.refresh_from_db()
        self.assertEqual(self.non.quantity, Decimal("46"))

    def test_refund_is_logged_as_stock_movement(self):
        refund(sale=self.sale, rows={self.non_item.pk: 1}, user=self.cashier)
        movement = StockMovement.objects.get(kind=StockMovement.Kind.QAYTARISH)
        self.assertEqual(movement.quantity, Decimal("1"))
        self.assertEqual(movement.product, self.non)

    def test_refund_of_discounted_row_uses_paid_price(self):
        sale = self.sell([{"product": self.sut.pk, "quantity": 2}], discount_amount=Decimal("4000"))
        item = sale.items.get()
        amount = refund(sale=sale, rows={item.pk: 1}, user=self.cashier)
        self.assertEqual(amount, Decimal("10000"))      # (24000 − 4000) / 2

    def test_refund_of_debt_sale_reduces_debt(self):
        customer = Customer.objects.create(company=self.company, full_name="Qarzdor")
        sale = self.sell([{"product": self.sut.pk, "quantity": 2}],
                         payment_method=PaymentMethod.QARZ, customer=customer)
        item = sale.items.get()
        refund(sale=sale, rows={item.pk: 1}, user=self.cashier)

        customer.refresh_from_db()
        self.assertEqual(customer.debt, Decimal("12000"))


class AccessTests(SalesMixin, TestCase):
    """Mijoz rolidagi foydalanuvchi ichki sahifalarni ko'rmasligi kerak."""

    PAGES = ["dashboard", "product_list", "sale_list", "customer_list", "reports"]

    def test_staff_sees_the_back_office(self):
        self.client.force_login(self.cashier)
        for name in self.PAGES:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_customer_role_is_turned_away(self):
        mijoz = User.objects.create_user(
            username="xaridor", password="parol12345",
            role=User.Role.MIJOZ, company=self.company,
        )
        self.client.force_login(mijoz)
        for name in self.PAGES:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 302)


class PosViewTests(SalesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.cashier)

    def test_pos_page_opens_for_seller(self):
        self.assertEqual(self.client.get(reverse("pos")).status_code, 200)

    def test_pos_is_closed_for_customers(self):
        mijoz = User.objects.create_user(
            username="mijoz2", password="parol12345", role=User.Role.MIJOZ, company=self.company
        )
        self.client.force_login(mijoz)
        self.assertEqual(self.client.get(reverse("pos")).status_code, 302)

    def test_search_finds_product_by_name(self):
        data = self.client.get(reverse("pos_search"), {"q": "sut"}).json()
        self.assertEqual([r["name"] for r in data["results"]], ["Sut 1L"])

    def test_exact_barcode_returns_single_result(self):
        data = self.client.get(reverse("pos_search"), {"q": "1002"}).json()
        self.assertTrue(data["exact"])
        self.assertEqual(len(data["results"]), 1)

    def test_search_does_not_leak_other_branches(self):
        data = self.client.get(reverse("pos_search"), {"q": "Shakar"}).json()
        self.assertEqual(data["results"], [])

    def _checkout(self, payload):
        return self.client.post(
            reverse("pos_checkout"), data=json.dumps(payload), content_type="application/json"
        )

    def test_checkout_endpoint_creates_sale(self):
        response = self._checkout({
            "items": [{"product": self.non.pk, "quantity": 2}],
            "payment_method": PaymentMethod.NAQD,
            "paid_amount": "10000",
        })
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["change"], 2000)
        self.assertEqual(Sale.objects.count(), 1)

    def test_checkout_answer_describes_the_sale(self):
        """Yakunlangandan keyingi oyna shu maydonlardan to'ldiriladi."""
        body = self._checkout({
            "items": [{"product": self.non.pk, "quantity": 2}],
            "payment_method": PaymentMethod.KARTA,
        }).json()

        self.assertEqual(body["number"], 1)
        self.assertEqual(body["total"], 8000)
        self.assertEqual(body["payment"], "Plastik karta")
        self.assertEqual(body["customer"], "")
        self.assertIsNone(body["debt"])          # qarz emas — qator ko'rsatilmaydi

    def test_debt_checkout_answer_carries_the_new_debt(self):
        customer = Customer.objects.create(company=self.company, full_name="Qarzdor Mijoz")
        body = self._checkout({
            "items": [{"product": self.non.pk, "quantity": 2}],
            "payment_method": PaymentMethod.QARZ,
            "customer": customer.pk,
        }).json()

        self.assertEqual(body["payment"], "Qarzga")
        self.assertEqual(body["customer"], "Qarzdor Mijoz")
        self.assertEqual(body["debt"], 8000)

    def test_checkout_endpoint_reports_errors(self):
        response = self._checkout({
            "items": [{"product": self.non.pk, "quantity": 9999}],
            "payment_method": PaymentMethod.KARTA,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("qoldig'i", response.json()["error"])
        self.assertEqual(Sale.objects.count(), 0)


class SalePageTests(SalesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.sale = self.sell()
        self.client.force_login(self.cashier)

    def test_sale_list_shows_the_check(self):
        html = self.client.get(reverse("sale_list")).content.decode()
        self.assertIn(f"№{self.sale.number}", html)

    def test_sale_detail_and_receipt_open(self):
        self.assertEqual(self.client.get(reverse("sale_detail", args=[self.sale.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("sale_receipt", args=[self.sale.pk])).status_code, 200)

    def test_other_branch_sale_is_not_visible(self):
        other = checkout(
            user=self.make_director(), branch=self.food_branch,
            rows=[{"product": self.boshqa.pk, "quantity": 1}],
            payment_method=PaymentMethod.NAQD,
        )
        self.assertEqual(self.client.get(reverse("sale_detail", args=[other.pk])).status_code, 404)

    def test_refund_through_the_page(self):
        item = self.sale.items.get()
        self.client.post(reverse("sale_refund", args=[self.sale.pk]),
                         {f"qty-{item.pk}": "1", "note": "sifatsiz"})
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.refunded_amount, Decimal("4000"))
        self.assertIn("sifatsiz", self.sale.note)

    def test_date_filter_narrows_the_list(self):
        response = self.client.get(reverse("sale_list"), {"dan": "2000-01-01", "gacha": "2000-01-31"})
        self.assertEqual(list(response.context["sales"]), [])


# ---------------------------------------------------------------------------
# Mijozlar
# ---------------------------------------------------------------------------

class CustomerViewTests(SalesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.cashier)
        self.customer = Customer.objects.create(
            company=self.company, full_name="Dilshod Aliyev", phone="+998901234567"
        )

    def test_customer_is_created(self):
        response = self.client.post(reverse("customer_create"), {
            "full_name": "Yangi Mijoz", "phone": "+998907654321", "address": "Toshkent",
            "discount_percent": "5", "debt_limit": "0", "note": "", "is_active": "on",
        })
        customer = Customer.objects.get(full_name="Yangi Mijoz")
        self.assertRedirects(response, reverse("customer_detail", args=[customer.pk]))
        self.assertEqual(customer.company, self.company)

    def test_discount_above_hundred_is_rejected(self):
        self.client.post(reverse("customer_create"), {
            "full_name": "Xato", "discount_percent": "150", "debt_limit": "0", "is_active": "on",
        })
        self.assertFalse(Customer.objects.filter(full_name="Xato").exists())

    def test_detail_page_shows_purchase_history(self):
        self.sell(customer=self.customer)
        html = self.client.get(reverse("customer_detail", args=[self.customer.pk])).content.decode()
        self.assertIn("Dilshod Aliyev", html)
        self.assertIn("№1", html)

    def test_debt_payment_through_the_page(self):
        self.sell(payment_method=PaymentMethod.QARZ, customer=self.customer)
        response = self.client.post(reverse("customer_pay", args=[self.customer.pk]),
                                    {"amount": "3000", "note": "qisman"}, follow=True)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.debt, Decimal("5000"))
        # Xabardagi summa sahifadagi kabi ajratgich bilan chiqadi.
        # Ajratgich belgisi tilga bog'liq, shuning uchun uni ham intcomma beradi.
        self.assertContains(response, f"Qolgan qarz: {intcomma('5000')}")
        self.assertNotContains(response, "Qolgan qarz: 5000")

    def test_debt_filter_shows_only_debtors(self):
        self.sell(payment_method=PaymentMethod.QARZ, customer=self.customer)
        Customer.objects.create(company=self.company, full_name="Qarzsiz")

        names = [c.full_name for c in
                 self.client.get(reverse("customer_list"), {"qarz": "1"}).context["customers"]]
        self.assertEqual(names, ["Dilshod Aliyev"])


# ---------------------------------------------------------------------------
# Ombor va hisobotlar
# ---------------------------------------------------------------------------

class StockAndProductEditTests(SalesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.make_director())

    def test_edit_page_saves_changes(self):
        self.client.post(reverse("product_edit", args=[self.non.pk]), {
            "branch": self.branch.pk, "name": "Non (bug'doy)", "sku": "", "unit": "dona",
            "cost_price": "2500", "price": "4500", "min_quantity": "5",
            "barcode": "1001", "color": "Oq", "color_hex": "#ffffff", "is_active": "on",
        })
        self.non.refresh_from_db()
        self.assertEqual(self.non.name, "Non (bug'doy)")
        self.assertEqual(self.non.price, Decimal("4500"))
        self.assertEqual(self.non.color.name, "Oq")

    def test_duplicate_barcode_is_rejected(self):
        self.client.post(reverse("product_edit", args=[self.non.pk]), {
            "branch": self.branch.pk, "name": "Non", "sku": "", "unit": "dona",
            "cost_price": "2000", "price": "4000", "min_quantity": "0",
            "barcode": "1002", "is_active": "on",      # Sut 1L kodini olmoqchi
        })
        self.non.refresh_from_db()
        self.assertEqual(self.non.barcode, "1001")

    def test_stock_intake_adds_quantity_and_logs_it(self):
        self.client.post(reverse("product_edit", args=[self.non.pk]), {
            "save_stock": "1", "kind": StockMovement.Kind.KIRIM,
            "quantity": "20", "cost_price": "2200", "note": "ta'minotchidan",
        })
        self.non.refresh_from_db()
        self.assertEqual(self.non.quantity, Decimal("70"))
        self.assertEqual(self.non.cost_price, Decimal("2200"))
        self.assertEqual(
            StockMovement.objects.get(product=self.non, kind=StockMovement.Kind.KIRIM).note,
            "ta'minotchidan",
        )

    def test_correction_sets_the_exact_quantity(self):
        self.client.post(reverse("product_edit", args=[self.non.pk]), {
            "save_stock": "1", "kind": StockMovement.Kind.TUZATISH, "quantity": "45", "note": "sanoq",
        })
        self.non.refresh_from_db()
        self.assertEqual(self.non.quantity, Decimal("45"))

    def test_unsold_product_is_deleted(self):
        self.client.post(reverse("product_delete", args=[self.non.pk]))
        self.assertFalse(Product.objects.filter(pk=self.non.pk).exists())

    def test_sold_product_is_archived_instead_of_deleted(self):
        self.sell([{"product": self.non.pk, "quantity": 1}])
        self.client.post(reverse("product_delete", args=[self.non.pk]))

        self.non.refresh_from_db()
        self.assertFalse(self.non.is_active)

    def test_cashier_cannot_touch_other_branch_product(self):
        self.client.force_login(self.make_cashier("chetdagi", branches=[self.branch]))
        self.assertEqual(
            self.client.get(reverse("product_edit", args=[self.boshqa.pk])).status_code, 404
        )


class ReportTests(SalesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.make_director())

    def test_report_totals_match_the_sales(self):
        self.sell([{"product": self.non.pk, "quantity": 3}])      # 12000, tannarx 6000
        self.sell([{"product": self.sut.pk, "quantity": 1}])      # 12000, tannarx 8000

        stats = self.client.get(reverse("reports")).context["stats"]
        self.assertEqual(stats["revenue"], Decimal("24000"))
        self.assertEqual(stats["profit"], Decimal("10000"))
        self.assertEqual(stats["checks"], 2)

    def test_refunded_part_is_removed_from_revenue(self):
        sale = self.sell([{"product": self.non.pk, "quantity": 4}])   # 16000
        refund(sale=sale, rows={sale.items.get().pk: 2}, user=self.cashier)

        stats = self.client.get(reverse("reports")).context["stats"]
        self.assertEqual(stats["revenue"], Decimal("8000"))
        self.assertEqual(stats["profit"], Decimal("4000"))

    def test_dashboard_shows_today_numbers(self):
        self.sell([{"product": self.sut.pk, "quantity": 2}])
        context = self.client.get(reverse("dashboard")).context
        self.assertEqual(context["stats"]["revenue"], Decimal("24000"))
        self.assertEqual(context["stats"]["checks"], 1)
        self.assertTrue(context["has_data"])

    def test_low_stock_products_are_listed(self):
        self.non.min_quantity = Decimal("60")
        self.non.save(update_fields=["min_quantity"])

        alerts = self.client.get(reverse("dashboard")).context["alerts"]
        self.assertEqual(alerts["low_count"], 1)
        self.assertEqual(alerts["low"].get(), self.non)

    def test_payment_breakdown_groups_by_method(self):
        self.sell(payment_method=PaymentMethod.NAQD, paid_amount="8000")
        self.sell(payment_method=PaymentMethod.KARTA)

        payments = self.client.get(reverse("reports")).context["payments"]
        self.assertEqual({p["label"] for p in payments}, {"Naqd", "Plastik karta"})

    def test_cashier_breakdown_counts_checks(self):
        self.sell()
        self.sell()
        rows = self.client.get(reverse("reports")).context["cashiers"]
        self.assertEqual(rows[0]["checks"], 2)

    def test_report_period_filter_is_applied(self):
        self.sell()
        response = self.client.get(reverse("reports"), {"dan": "2000-01-01", "gacha": "2000-01-31"})
        self.assertEqual(response.context["stats"]["checks"], 0)


# ---------------------------------------------------------------------------
# Formalardagi raqamli maydonlar
# ---------------------------------------------------------------------------

class NumberInputTests(BaseDataMixin, TestCase):
    """Brauzer yaroqli qiymatlarni `min + n × step` deb hisoblaydi.

    Shuning uchun `min` `step` ga bo'linmasa, foydalanuvchi butun summani
    (masalan 20 000) kirita olmay qoladi — forma serverga umuman yetib kelmaydi.
    """

    def _forms(self):
        director = self.make_director()
        customer = Customer.objects.create(
            company=self.company, full_name="Mijoz", debt=Decimal("390000")
        )
        return [
            CustomerPaymentForm(customer=customer),
            CustomerForm(),
            StockAdjustForm(),
            VariantForm(),
            ProductBaseForm(user=director),
            ProductEditForm(user=director),
        ]

    def test_min_is_a_multiple_of_step(self):
        for form in self._forms():
            for name, field in form.fields.items():
                attrs = field.widget.attrs
                step, minimum = attrs.get("step"), attrs.get("min")
                if step in (None, "any") or minimum in (None, ""):
                    continue
                with self.subTest(form=type(form).__name__, field=name):
                    self.assertEqual(
                        Decimal(str(minimum)) % Decimal(str(step)), Decimal("0"),
                        f"min={minimum} step={step} — oraliq qiymatlar rad etiladi",
                    )

    def test_round_payment_is_accepted(self):
        customer = Customer.objects.create(
            company=self.company, full_name="Qarzdor", debt=Decimal("390000")
        )
        form = CustomerPaymentForm({"amount": "20000", "note": ""}, customer=customer)
        self.assertTrue(form.is_valid(), form.errors)

    def test_fractional_quantity_is_allowed_for_weighed_goods(self):
        form = StockAdjustForm({
            "kind": StockMovement.Kind.KIRIM, "quantity": "2.5", "note": "",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["quantity"], Decimal("2.5"))


# ---------------------------------------------------------------------------
# Rasm siqish
# ---------------------------------------------------------------------------

class ImageCompressionTests(BaseDataMixin, TestCase):
    """Siqish fayl hajmini kamaytirsin, lekin nisbat va sifat buzilmasin."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)
        super().tearDownClass()

    def _photo(self, size, mode="RGB", fmt="JPEG", quality=95):
        width, height = size
        image = Image.new(mode, size)
        pixels = image.load()
        for x in range(width):
            for y in range(height):
                value = ((x * 255) // width, (y * 255) // height, ((x + y) * 255) // (width + height))
                pixels[x, y] = value + ((x * 255) // width,) if mode == "RGBA" else value
        buffer = io.BytesIO()
        image.save(buffer, format=fmt, quality=quality)
        return buffer.getvalue()

    def _make(self, raw, filename):
        return Product.objects.create(
            branch=self.branch, name="Rasmli mahsulot", price=Decimal("1000"),
            image=SimpleUploadedFile(filename, raw, content_type="image/jpeg"),
        )

    def test_large_photo_is_shrunk_but_keeps_aspect_ratio(self):
        raw = self._photo((3000, 2000))
        product = self._make(raw, "katta.jpg")
        with Image.open(product.image.path) as result:
            self.assertEqual(max(result.size), settings.POS_IMAGE_MAX_SIDE)
            self.assertAlmostEqual(result.size[0] / result.size[1], 1.5, places=2)
        self.assertLess(product.image.size, len(raw))

    def test_small_image_is_not_upscaled(self):
        product = self._make(self._photo((300, 200)), "kichik.jpg")
        with Image.open(product.image.path) as result:
            self.assertEqual(result.size, (300, 200))

    def test_transparency_is_preserved(self):
        raw = self._photo((1200, 900), mode="RGBA", fmt="PNG", quality=None)
        product = Product.objects.create(
            branch=self.branch, name="Shaffof", price=Decimal("1000"),
            image=SimpleUploadedFile("shaffof.png", raw, content_type="image/png"),
        )
        self.assertTrue(product.image.name.endswith(".webp"))
        with Image.open(product.image.path) as result:
            self.assertIn("A", result.mode)
            self.assertEqual(result.size, (1200, 900))
        self.assertLess(product.image.size, len(raw))

    def test_opaque_rgba_becomes_jpeg(self):
        width, height = 900, 600
        rnd = random.Random(7)
        image = Image.new("RGBA", (width, height))
        pixels = image.load()
        for x in range(width):
            for y in range(height):
                pixels[x, y] = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256), 255)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        raw = buffer.getvalue()

        product = Product.objects.create(
            branch=self.branch, name="Shaffofsiz", price=Decimal("1000"),
            image=SimpleUploadedFile("shaffofsiz.png", raw, content_type="image/png"),
        )
        self.assertTrue(product.image.name.endswith(".jpg"))
        self.assertLess(product.image.size, len(raw))

    def test_already_light_file_is_left_alone(self):
        raw = self._photo((60, 60), quality=30)
        product = self._make(raw, "yengil.jpg")
        self.assertEqual(product.image.size, len(raw))

    def test_rotated_photo_is_straightened(self):
        image = Image.new("RGB", (400, 200), "#336699")
        exif = image.getexif()
        exif[274] = 6
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", exif=exif)

        product = self._make(buffer.getvalue(), "burilgan.jpg")
        with Image.open(product.image.path) as result:
            self.assertEqual(result.size, (200, 400))


# ---------------------------------------------------------------------------
# Nakladnoylar: qabul va sotuv
# ---------------------------------------------------------------------------

from unittest import mock

from . import analysis, documents
from .models import Supplier, Waybill, WaybillItem
from .services import WaybillError, confirm_receipt, confirm_sale


class WaybillMixin(SalesMixin):
    """Nakladnoy testlari: AI chaqirilmaydi — qatorlar to'g'ridan-to'g'ri yaratiladi."""

    def setUp(self):
        super().setUp()
        self.director = self.make_director()
        self.supplier = Supplier.objects.create(company=self.company, name="Textile Trade")

    def waybill(self, kind=Waybill.Kind.QABUL, rows=(), **kwargs):
        waybill = Waybill.objects.create(
            branch=self.branch, kind=kind, created_by=self.director, **kwargs
        )
        for row in rows:
            WaybillItem.objects.create(waybill=waybill, **row)
        return waybill


class ReceiptTests(WaybillMixin, TestCase):
    def test_confirm_increases_stock_and_updates_cost(self):
        waybill = self.waybill(rows=[
            {"product": self.non, "name": "Non", "quantity": Decimal("10"), "price": Decimal("2500")},
        ])
        confirm_receipt(waybill, self.director)

        self.non.refresh_from_db()
        self.assertEqual(self.non.quantity, Decimal("60"))
        self.assertEqual(self.non.cost_price, Decimal("2500"))
        movement = StockMovement.objects.get(product=self.non, kind=StockMovement.Kind.KIRIM)
        self.assertEqual(movement.quantity, Decimal("10"))
        waybill.refresh_from_db()
        self.assertEqual(waybill.status, Waybill.Status.TASDIQLANGAN)
        self.assertEqual(waybill.confirmed_by, self.director)

    def test_unmatched_row_creates_product(self):
        waybill = self.waybill(rows=[
            {"name": "Shapka qishki", "unit": "dona", "quantity": Decimal("6"),
             "price": Decimal("45000"), "sale_price": Decimal("65000")},
        ])
        confirm_receipt(waybill, self.director)

        product = Product.objects.get(branch=self.branch, name="Shapka qishki")
        self.assertEqual(product.quantity, Decimal("6"))
        self.assertEqual(product.cost_price, Decimal("45000"))
        self.assertEqual(product.price, Decimal("65000"))
        self.assertEqual(waybill.items.get().product, product)

    def test_unmatched_row_without_sale_price_changes_nothing(self):
        waybill = self.waybill(rows=[
            {"product": self.non, "name": "Non", "quantity": Decimal("10"), "price": Decimal("2500")},
            {"name": "Noma'lum tovar", "quantity": Decimal("3"), "price": Decimal("1000")},
        ])
        with self.assertRaises(WaybillError):
            confirm_receipt(waybill, self.director)

        # Birinchi qator ham qo'llanmagan — hammasi yoki hech narsa.
        self.non.refresh_from_db()
        self.assertEqual(self.non.quantity, Decimal("50"))
        waybill.refresh_from_db()
        self.assertEqual(waybill.status, Waybill.Status.QORALAMA)

    def test_on_credit_adds_supplier_debt(self):
        waybill = self.waybill(supplier=self.supplier, on_credit=True, rows=[
            {"product": self.sut, "name": "Sut", "quantity": Decimal("5"), "price": Decimal("8000")},
        ])
        confirm_receipt(waybill, self.director)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.debt, Decimal("40000"))

    def test_on_credit_requires_supplier(self):
        waybill = self.waybill(on_credit=True, rows=[
            {"product": self.sut, "name": "Sut", "quantity": Decimal("5"), "price": Decimal("8000")},
        ])
        with self.assertRaises(WaybillError):
            confirm_receipt(waybill, self.director)
        self.sut.refresh_from_db()
        self.assertEqual(self.sut.quantity, Decimal("20"))

    def test_cannot_confirm_twice(self):
        waybill = self.waybill(rows=[
            {"product": self.non, "name": "Non", "quantity": Decimal("1"), "price": Decimal("2000")},
        ])
        confirm_receipt(waybill, self.director)
        with self.assertRaises(WaybillError):
            confirm_receipt(waybill, self.director)
        self.non.refresh_from_db()
        self.assertEqual(self.non.quantity, Decimal("51"))

    def test_foreign_branch_product_rejected(self):
        waybill = self.waybill(rows=[
            {"product": self.boshqa, "name": "Shakar", "quantity": Decimal("1"), "price": Decimal("9000")},
        ])
        with self.assertRaises(WaybillError):
            confirm_receipt(waybill, self.director)


class WaybillSaleTests(WaybillMixin, TestCase):
    def test_confirm_creates_sale_through_checkout(self):
        waybill = self.waybill(kind=Waybill.Kind.SOTUV, rows=[
            {"product": self.non, "name": "Non", "quantity": Decimal("5"), "price": Decimal("3800")},
            {"product": self.sut, "name": "Sut", "quantity": Decimal("2"), "price": Decimal("0")},
        ])
        confirm_sale(waybill, self.cashier, payment_method=PaymentMethod.OTKAZMA)

        waybill.refresh_from_db()
        sale = waybill.sale
        self.assertEqual(waybill.status, Waybill.Status.TASDIQLANGAN)
        # Narx 0 bo'lsa — mahsulotning joriy narxi olinadi.
        self.assertEqual(sale.total, Decimal("5") * Decimal("3800") + Decimal("2") * Decimal("12000"))
        self.non.refresh_from_db()
        self.assertEqual(self.non.quantity, Decimal("45"))

    def test_unmatched_row_blocks_sale(self):
        waybill = self.waybill(kind=Waybill.Kind.SOTUV, rows=[
            {"name": "Noma'lum", "quantity": Decimal("1"), "price": Decimal("1000")},
        ])
        with self.assertRaises(WaybillError):
            confirm_sale(waybill, self.cashier, payment_method=PaymentMethod.NAQD)
        self.assertEqual(Sale.objects.count(), 0)

    def test_insufficient_stock_keeps_draft(self):
        waybill = self.waybill(kind=Waybill.Kind.SOTUV, rows=[
            {"product": self.sut, "name": "Sut", "quantity": Decimal("999"), "price": Decimal("12000")},
        ])
        with self.assertRaises(WaybillError):
            confirm_sale(waybill, self.cashier, payment_method=PaymentMethod.KARTA)
        waybill.refresh_from_db()
        self.assertEqual(waybill.status, Waybill.Status.QORALAMA)
        self.assertIsNone(waybill.sale)

    def test_debt_sale_requires_customer(self):
        waybill = self.waybill(kind=Waybill.Kind.SOTUV, rows=[
            {"product": self.non, "name": "Non", "quantity": Decimal("1"), "price": Decimal("4000")},
        ])
        with self.assertRaises(WaybillError):
            confirm_sale(waybill, self.cashier, payment_method=PaymentMethod.QARZ)


class WaybillViewTests(WaybillMixin, TestCase):
    def form_data(self, waybill, action, **overrides):
        """Tahrirlash formasi — sahifada qanday yuborilsa shunday."""
        items = list(waybill.items.all())
        data = {
            "action": action,
            "number": waybill.number,
            "doc_date": "",
            "note": "",
            "items-TOTAL_FORMS": str(len(items)),
            "items-INITIAL_FORMS": str(len(items)),
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
        }
        if waybill.kind == Waybill.Kind.SOTUV:
            data["customer"] = ""
            data["payment_method"] = str(PaymentMethod.KARTA)
        else:
            data["supplier"] = ""
        for index, item in enumerate(items):
            prefix = f"items-{index}-"
            data.update({
                prefix + "id": str(item.pk),
                prefix + "product": str(item.product_id or ""),
                prefix + "name": item.name,
                prefix + "barcode": item.barcode,
                prefix + "unit": item.unit,
                prefix + "quantity": str(item.quantity),
                prefix + "price": str(item.price),
                prefix + "sale_price": str(item.sale_price or ""),
            })
        data.update(overrides)
        return data

    def test_confirm_from_page_with_edited_quantity(self):
        waybill = self.waybill(rows=[
            {"product": self.non, "name": "Non", "quantity": Decimal("10"), "price": Decimal("2000")},
        ])
        self.client.force_login(self.director)
        url = reverse("waybill_detail", args=[waybill.pk])
        # Foydalanuvchi AI o'qigan sonni tuzatadi: 10 → 12.
        response = self.client.post(url, self.form_data(waybill, "confirm", **{"items-0-quantity": "12"}))
        self.assertRedirects(response, url)
        self.non.refresh_from_db()
        self.assertEqual(self.non.quantity, Decimal("62"))

    def test_cancel_does_not_touch_stock(self):
        waybill = self.waybill(rows=[
            {"product": self.non, "name": "Non", "quantity": Decimal("10"), "price": Decimal("2000")},
        ])
        self.client.force_login(self.director)
        self.client.post(reverse("waybill_detail", args=[waybill.pk]), {"action": "cancel"})
        waybill.refresh_from_db()
        self.assertEqual(waybill.status, Waybill.Status.BEKOR)
        self.non.refresh_from_db()
        self.assertEqual(self.non.quantity, Decimal("50"))

    def test_sale_waybill_redirects_to_sale(self):
        waybill = self.waybill(kind=Waybill.Kind.SOTUV, rows=[
            {"product": self.non, "name": "Non", "quantity": Decimal("2"), "price": Decimal("4000")},
        ])
        self.client.force_login(self.director)
        response = self.client.post(
            reverse("waybill_detail", args=[waybill.pk]), self.form_data(waybill, "confirm")
        )
        waybill.refresh_from_db()
        self.assertRedirects(response, reverse("sale_detail", args=[waybill.sale.pk]))

    def test_cashier_cannot_open_receipt(self):
        waybill = self.waybill(rows=[
            {"product": self.non, "name": "Non", "quantity": Decimal("1"), "price": Decimal("2000")},
        ])
        self.client.force_login(self.cashier)
        response = self.client.get(reverse("waybill_detail", args=[waybill.pk]))
        self.assertRedirects(response, reverse("waybill_list"))

    def test_supplier_pages_need_receive_right(self):
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("supplier_list")).status_code, 302)
        self.client.force_login(self.director)
        self.assertEqual(self.client.get(reverse("supplier_list")).status_code, 200)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class WaybillUploadTests(WaybillMixin, TestCase):
    """Yuklash: AI chaqiruvi `parse_waybill` darajasida almashtiriladi."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def fake_parse(self, waybill):
        waybill.number = "145"
        waybill.supplier_name = '"Textile Trade" MChJ'
        waybill.save()
        WaybillItem.objects.create(
            waybill=waybill, product=self.non, name="Non", quantity=Decimal("3"),
            price=Decimal("2000"), matched_by=WaybillItem.Match.NOM,
        )
        return {"items": 1, "matched": 1, "warnings": []}

    def upload(self, name="n.png", content=b"fake"):
        return SimpleUploadedFile(name, content, content_type="image/png")

    def test_page_upload_creates_draft_and_guesses_supplier(self):
        self.client.force_login(self.director)
        with mock.patch.object(documents, "parse_waybill", side_effect=self.fake_parse):
            response = self.client.post(reverse("waybill_upload"), {"kind": 1, "file": self.upload()})

        waybill = Waybill.objects.get()
        self.assertRedirects(response, reverse("waybill_detail", args=[waybill.pk]))
        self.assertEqual(waybill.status, Waybill.Status.QORALAMA)
        # Qo'shtirnoq va "MChJ" olib tashlanib solishtiriladi.
        self.assertEqual(waybill.supplier, self.supplier)
        self.non.refresh_from_db()
        self.assertEqual(self.non.quantity, Decimal("50"))

    def test_wrong_file_type_rejected(self):
        self.client.force_login(self.director)
        self.client.post(reverse("waybill_upload"), {
            "kind": 1, "file": SimpleUploadedFile("x.exe", b"MZ", content_type="application/octet-stream"),
        })
        self.assertEqual(Waybill.objects.count(), 0)

    def test_chat_command_returns_review_link(self):
        self.client.force_login(self.director)
        with mock.patch.object(documents, "parse_waybill", side_effect=self.fake_parse):
            response = self.client.post(reverse("chat_send"), {
                "message": "/qabul", "file": self.upload(),
            })
        data = response.json()
        waybill = Waybill.objects.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["tables"][0]["havola"], reverse("waybill_detail", args=[waybill.pk]))
        self.assertIn("tasdiqlang", data["reply"])

    def test_chat_command_without_file(self):
        self.client.force_login(self.director)
        response = self.client.post(
            reverse("chat_send"), json.dumps({"message": "/qabul"}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("faylini biriktiring", response.json()["error"])

    def test_cashier_cannot_receive_via_chat(self):
        self.client.force_login(self.cashier)
        with mock.patch.object(documents, "parse_waybill", side_effect=self.fake_parse):
            response = self.client.post(reverse("chat_send"), {"message": "/qabul", "file": self.upload()})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Waybill.objects.count(), 0)


class DocumentHelperTests(TestCase):
    """AI'siz ishlaydigan yordamchilar: narx hisobi va birliklar."""

    def item(self, **kwargs):
        return documents.ParsedItem(name="Tovar", **kwargs)

    def test_price_from_total(self):
        price, warning = documents._prices(self.item(quantity=4, total=1000000))
        self.assertEqual(price, Decimal("250000.00"))
        self.assertIsNone(warning)

    def test_mismatch_is_reported(self):
        price, warning = documents._prices(self.item(quantity=4, price=250000, total=900000))
        self.assertEqual(price, Decimal("250000.00"))
        self.assertIn("900000", warning)

    def test_rounding_difference_ignored(self):
        _, warning = documents._prices(self.item(quantity=3, price=333.33, total=1000))
        self.assertIsNone(warning)

    def test_units_normalized(self):
        self.assertEqual(documents._unit("шт."), "dona")
        self.assertEqual(documents._unit("КГ"), "kg")


class AnalysisSandboxTests(TestCase):
    """Model yozgan kodni tekshirish — API'siz."""

    def frame(self):
        import pandas as pd
        return pd.DataFrame({"mahsulot": ["A", "B", "A"], "tushum": [10.0, 20.0, 5.0]})

    def test_clean_code_runs(self):
        code = "def javob(df):\n    return df.groupby('mahsulot')['tushum'].sum()"
        shaped, table = analysis.shape(analysis.run(code, self.frame()))
        self.assertEqual(shaped["ustunlar"], ["mahsulot", "tushum"])
        self.assertIsNone(table)

    def test_dangerous_code_rejected(self):
        cases = [
            "import os\ndef javob(df):\n    return 1",
            "def javob(df):\n    return df.__class__",
            "def javob(df):\n    return df.to_csv('/tmp/x')",
            "def javob(df):\n    return open('/etc/passwd').read()",
            "def javob(df):\n    while True:\n        pass",
        ]
        for code in cases:
            with self.subTest(code=code.splitlines()[-1]):
                with self.assertRaises(analysis.UnsafeCode):
                    analysis.run(code, self.frame())

    def test_groupby_index_kept_as_column(self):
        code = "def javob(df):\n    return df.groupby('mahsulot').agg({'tushum': 'sum'})"
        shaped, _ = analysis.shape(analysis.run(code, self.frame()))
        self.assertIn("mahsulot", shaped["ustunlar"])
