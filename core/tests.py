import io
import random
import shutil
import tempfile
from decimal import Decimal

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.utils import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Branch, Color, Company, Product, TradeType, User


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
