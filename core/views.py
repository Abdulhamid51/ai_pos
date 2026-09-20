import json
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.humanize.templatetags.humanize import intcomma
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import reporting
from .forms import (
    BranchForm,
    BranchSwitchForm,
    CompanySettingsForm,
    CustomerForm,
    CustomerPaymentForm,
    ProductBaseForm,
    ProductEditForm,
    StaffForm,
    StockAdjustForm,
    VariantFormSet,
)
from .models import (
    TRADE_TYPE_FIELDS,
    Branch,
    Color,
    Customer,
    PaymentMethod,
    Product,
    Sale,
    StockMovement,
    User,
    apply_stock,
)
from .services import CheckoutError, checkout, pay_debt, refund

# Direktor (yoki superuser) uchun cheklov.
manager_required = user_passes_test(lambda u: u.is_authenticated and u.can_manage())

# Kassa va qaytarish — sotish huquqi bor rollar uchun.
seller_required = user_passes_test(lambda u: u.is_authenticated and u.can_sell())

# Ichki sahifalar — mijoz rolidagi foydalanuvchiga ochilmaydi.
staff_required = user_passes_test(lambda u: u.is_authenticated and u.is_xodim)


def _back(request, panel=""):
    """Sozlamalar modalidan kelgan formadan keyin o'sha sahifaga qaytarish."""
    target = request.POST.get("next") or "/"
    return redirect(f"{target}#settings/{panel}" if panel else target)


def _money(amount):
    """Xabarlardagi summa sahifadagi kabi ajratgich bilan ko'rsatilsin."""
    return f"{intcomma(f'{amount:.0f}')} {settings.POS_CURRENCY}"


def _company_of(user):
    """Foydalanuvchining kompaniyasi — filial orqali ham aniqlanadi."""
    if user.company_id:
        return user.company
    branch = user.active_branch
    return branch.company if branch else None


# ---------------------------------------------------------------------------
# Boshqaruv paneli
# ---------------------------------------------------------------------------

@login_required
@staff_required
def dashboard(request):
    """Haqiqiy sotuv ma'lumotlari asosidagi boshqaruv paneli."""
    branches = request.user.visible_branches()
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

    all_sales = reporting.sales_of(branches)
    today_sales = all_sales.filter(created_at__date=today)
    yesterday_sales = all_sales.filter(created_at__date=yesterday)

    today_stats = reporting.summary(today_sales)
    yesterday_stats = reporting.summary(yesterday_sales)
    alerts = reporting.stock_alerts(branches)

    series = reporting.daily_series(all_sales, days=14, until=today)
    data = {
        **series,
        **reporting.hourly_series(today_sales),
        "payments": reporting.by_payment(all_sales.filter(created_at__date__gte=today.replace(day=1))),
        "top_products": reporting.top_products(
            all_sales.filter(created_at__date__gte=today - timedelta(days=6)), limit=5
        ),
    }

    company = _company_of(request.user)
    previous = yesterday_stats["revenue"]
    change = ((today_stats["revenue"] / previous - 1) * 100) if previous else None

    return render(request, "core/dashboard.html", {
        "page_title": "Boshqaruv paneli",
        "store_name": company.name if company else settings.POS_STORE_NAME,
        "currency": settings.POS_CURRENCY,
        "data": data,
        "stats": today_stats,
        "revenue_change": round(change, 1) if change is not None else None,
        "alerts": alerts,
        "debt": reporting.debt_summary(company),
        "chart_rows": list(zip(data["days"], data["revenue"], data["profit"])),
        "payments_total": sum(p["value"] for p in data["payments"]),
        "recent_sales": all_sales.select_related("branch", "cashier", "customer")[:8],
        "has_data": all_sales.exists(),
    })


# ---------------------------------------------------------------------------
# Mahsulotlar
# ---------------------------------------------------------------------------

@login_required
@staff_required
def product_list(request):
    visible = request.user.visible_branches()
    products = Product.objects.filter(branch__in=visible).select_related(
        "branch", "branch__company", "color"
    )

    query = request.GET.get("q", "").strip()
    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(barcode__icontains=query)
            | Q(sku__icontains=query) | Q(color__name__icontains=query)
            | Q(brand__icontains=query)
        )

    branch_id = request.GET.get("branch", "")
    if branch_id.isdigit():
        products = products.filter(branch_id=int(branch_id))

    page = Paginator(products, 24).get_page(request.GET.get("page"))

    return render(request, "core/product_list.html", {
        "page_title": "Mahsulotlar",
        "currency": settings.POS_CURRENCY,
        "page_obj": page,
        "products": page.object_list,
        "total": products.count(),
        "query": query,
        "branch_id": branch_id,
        "branches": visible.select_related("company"),
    })


@login_required
@staff_required
def product_create(request):
    if request.method == "POST":
        base_form = ProductBaseForm(request.POST, user=request.user)
        formset = VariantFormSet(request.POST, request.FILES, prefix="variant")

        if base_form.is_valid() and formset.is_valid():
            rows = [f for f in formset.forms if f.cleaned_data and f.is_filled()]
            if not rows:
                messages.error(request, "Kamida bitta rang qatorini to'ldiring.")
            else:
                base = base_form.cleaned_data
                shared = {
                    name: base.get(name)
                    for name in ProductBaseForm.Meta.fields
                    if name != "branch"
                }
                created = 0
                with transaction.atomic():
                    for row in rows:
                        Product.objects.create(
                            branch=base["branch"],
                            color=row.get_or_create_color(),
                            barcode=row.cleaned_data["barcode"],
                            quantity=row.cleaned_data["quantity"] or 0,
                            image=row.cleaned_data["image"] or "",
                            **{k: v for k, v in shared.items() if v is not None},
                        )
                        created += 1
                messages.success(request, f"\"{base['name']}\" uchun {created} ta mahsulot yaratildi.")
                return redirect("product_list")
    else:
        base_form = ProductBaseForm(user=request.user)
        formset = VariantFormSet(prefix="variant")

    # Filialni tanlaganda qaysi maydonlar ko'rinishini JS shu jadvaldan biladi.
    branch_trades = {
        str(b.pk): b.trade_type for b in request.user.visible_branches()
    }

    return render(request, "core/product_form.html", {
        "page_title": "Yangi mahsulot",
        "currency": settings.POS_CURRENCY,
        "form": base_form,
        "formset": formset,
        "colors": Color.objects.all(),
        "core_fields": ProductBaseForm.CORE_FIELDS,
        "branch_trades": branch_trades,
        "trade_fields": {str(int(k)): v for k, v in TRADE_TYPE_FIELDS.items()},
    })


@login_required
@staff_required
def product_edit(request, pk):
    """Bitta mahsulotni tahrirlash va shu yerdan ombor harakatini kiritish."""
    product = get_object_or_404(
        Product.objects.select_related("branch", "branch__company", "color"),
        pk=pk, branch__in=request.user.visible_branches(),
    )

    form = ProductEditForm(instance=product, user=request.user)
    stock_form = StockAdjustForm()

    if request.method == "POST":
        if "save_stock" in request.POST:
            stock_form = StockAdjustForm(request.POST)
            if stock_form.is_valid():
                delta = stock_form.delta_for(product)
                kind = stock_form.cleaned_data["kind"]
                cost = stock_form.cleaned_data.get("cost_price")
                if cost is not None and kind == StockMovement.Kind.KIRIM:
                    product.cost_price = cost
                    product.save(update_fields=["cost_price", "updated_at"])
                if delta:
                    apply_stock(product, delta, kind, user=request.user,
                                note=stock_form.cleaned_data["note"])
                    messages.success(
                        request,
                        f"Qoldiq yangilandi: {product.quantity:.0f} {product.get_unit_display()}.",
                    )
                else:
                    messages.info(request, "Qoldiq o'zgarmadi.")
                return redirect("product_edit", pk=product.pk)
        else:
            form = ProductEditForm(request.POST, request.FILES, instance=product, user=request.user)
            if form.is_valid():
                form.save()
                messages.success(request, f"\"{product.name}\" saqlandi.")
                return redirect("product_list")

    return render(request, "core/product_edit.html", {
        "page_title": product.name,
        "currency": settings.POS_CURRENCY,
        "product": product,
        "form": form,
        "stock_form": stock_form,
        "colors": Color.objects.all(),
        "core_fields": ProductEditForm.CORE_FIELDS,
        "trade_fields": product.branch.product_fields,
        "movements": product.movements.select_related("user", "sale")[:20],
        "sold_total": product.sale_items.aggregate(
            qty=Sum("quantity"), back=Sum("returned_quantity")
        ),
    })


@login_required
@staff_required
@require_POST
def product_delete(request, pk):
    """Mahsulotni o'chirish. Sotilgan bo'lsa — arxivga olinadi."""
    product = get_object_or_404(Product, pk=pk, branch__in=request.user.visible_branches())
    name = product.name

    if product.sale_items.exists():
        product.is_active = False
        product.save(update_fields=["is_active", "updated_at"])
        messages.warning(
            request,
            f"\"{name}\" cheklarda qatnashgan — o'chirilmadi, sotuvdan olindi.",
        )
    else:
        product.delete()
        messages.success(request, f"\"{name}\" o'chirildi.")
    return redirect("product_list")


# ---------------------------------------------------------------------------
# Kassa
# ---------------------------------------------------------------------------

def _product_json(product):
    return {
        "id": product.pk,
        "name": str(product),
        "barcode": product.barcode,
        "price": float(product.price),
        "quantity": float(product.quantity),
        "unit": product.get_unit_display(),
        "color": product.color.name if product.color_id else "",
        "hex": (product.color.hex if product.color_id else "") or "",
        "image": product.image.url if product.image else "",
        "low": product.is_low_stock,
    }


@login_required
@seller_required
def pos(request):
    """Kassa oynasi — savat brauzerda yig'iladi, yakunlash serverda tekshiriladi."""
    branch = request.user.active_branch
    if branch is None:
        messages.error(request, "Sizga filial biriktirilmagan — kassani ochib bo'lmaydi.")
        return redirect("dashboard")

    company = branch.company
    customers = Customer.objects.filter(company=company, is_active=True)

    return render(request, "core/pos.html", {
        "page_title": "Kassa",
        "currency": settings.POS_CURRENCY,
        "branch": branch,
        "customers": customers,
        "payment_methods": PaymentMethod.choices,
        "vat_percent": company.vat_percent,
        "allow_negative_stock": company.allow_negative_stock,
        "quick_products": Product.objects.filter(
            branch=branch, is_active=True, quantity__gt=0
        ).select_related("color")[:18],
        "last_sale_id": request.session.pop("last_sale_id", None),
    })


@login_required
@seller_required
def pos_search(request):
    """Kassadagi qidiruv: nomi, shtrix-kod yoki artikul bo'yicha."""
    branch = request.user.active_branch
    if branch is None:
        return JsonResponse({"results": []})

    query = request.GET.get("q", "").strip()
    products = Product.objects.filter(branch=branch, is_active=True).select_related("color")

    if query:
        exact = products.filter(barcode=query).first()
        if exact:
            # Skaner to'liq kodni yubordi — darhol shu mahsulot qaytariladi.
            return JsonResponse({"results": [_product_json(exact)], "exact": True})
        products = products.filter(
            Q(name__icontains=query) | Q(barcode__icontains=query)
            | Q(sku__icontains=query) | Q(color__name__icontains=query)
            | Q(brand__icontains=query)
        )

    return JsonResponse({
        "results": [_product_json(p) for p in products[:24]],
        "exact": False,
    })


@login_required
@seller_required
@require_POST
def pos_checkout(request):
    """Savatni yakunlaydi va chek yaratadi."""
    try:
        payload = json.loads(request.body.decode() or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "So'rov o'qilmadi."}, status=400)

    branch = request.user.active_branch
    customer = None
    customer_id = payload.get("customer")
    if customer_id:
        customer = Customer.objects.filter(
            pk=customer_id, company=branch.company if branch else None, is_active=True
        ).first()
        if customer is None:
            return JsonResponse({"ok": False, "error": "Mijoz topilmadi."}, status=400)

    try:
        sale = checkout(
            user=request.user,
            branch=branch,
            rows=payload.get("items") or [],
            payment_method=payload.get("payment_method"),
            paid_amount=payload.get("paid_amount"),
            discount_amount=payload.get("discount_amount") or Decimal("0"),
            customer=customer,
            note=payload.get("note") or "",
        )
    except CheckoutError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)

    request.session["last_sale_id"] = sale.pk
    return JsonResponse({
        "ok": True,
        "sale": sale.pk,
        "number": sale.number,
        "total": float(sale.total),
        "change": float(sale.change_amount),
        "payment": sale.get_payment_method_display(),
        "customer": customer.full_name if customer else "",
        # Qarzga sotilganda kassir mijozning yangi qarzini darhol ko'rsin.
        "debt": float(customer.debt) if (customer and sale.is_debt) else None,
        "receipt_url": reverse("sale_receipt", args=[sale.pk]),
    })


# ---------------------------------------------------------------------------
# Sotuvlar
# ---------------------------------------------------------------------------

@login_required
@staff_required
def sale_list(request):
    branches = request.user.visible_branches()
    since, until = reporting.day_bounds(request, default_days=6)

    sales = reporting.sales_of(branches, since, until).select_related(
        "branch", "cashier", "customer"
    )

    query = request.GET.get("q", "").strip()
    if query:
        sales = sales.filter(
            Q(number__icontains=query) | Q(customer__full_name__icontains=query)
            | Q(customer__phone__icontains=query) | Q(items__name__icontains=query)
        ).distinct()

    branch_id = request.GET.get("branch", "")
    if branch_id.isdigit():
        sales = sales.filter(branch_id=int(branch_id))

    payment = request.GET.get("payment", "")
    if payment.isdigit():
        sales = sales.filter(payment_method=int(payment))

    status = request.GET.get("status", "")
    if status.isdigit():
        sales = sales.filter(status=int(status))

    stats = reporting.summary(sales)
    page = Paginator(sales, 30).get_page(request.GET.get("page"))

    return render(request, "core/sale_list.html", {
        "page_title": "Sotuvlar",
        "currency": settings.POS_CURRENCY,
        "page_obj": page,
        "sales": page.object_list,
        "stats": stats,
        "query": query,
        "since": since,
        "until": until,
        "branch_id": branch_id,
        "payment": payment,
        "status": status,
        "branches": branches,
        "payment_methods": PaymentMethod.choices,
        "statuses": Sale.Status.choices,
    })


def _get_sale(request, pk):
    return get_object_or_404(
        Sale.objects.select_related("branch", "branch__company", "cashier", "customer"),
        pk=pk, branch__in=request.user.visible_branches(),
    )


@login_required
@staff_required
def sale_detail(request, pk):
    sale = _get_sale(request, pk)
    return render(request, "core/sale_detail.html", {
        "page_title": f"Chek №{sale.number}",
        "currency": settings.POS_CURRENCY,
        "sale": sale,
        "items": sale.items.select_related("product"),
        "can_refund": request.user.can_sell() and sale.status != Sale.Status.QAYTARILGAN,
    })


@login_required
@staff_required
def sale_receipt(request, pk):
    """Chop etiladigan chek — alohida, soddalashtirilgan sahifa."""
    sale = _get_sale(request, pk)
    return render(request, "core/receipt.html", {
        "sale": sale,
        "items": sale.items.all(),
        "company": sale.branch.company,
        "currency": settings.POS_CURRENCY,
        "auto_print": request.GET.get("chop") == "1",
    })


@login_required
@seller_required
@require_POST
def sale_refund(request, pk):
    """Chekdagi tanlangan qatorlarni qaytaradi."""
    sale = _get_sale(request, pk)

    rows = {}
    for key, value in request.POST.items():
        if key.startswith("qty-") and value:
            rows[key[4:]] = value

    try:
        amount = refund(
            sale=sale, rows=rows, user=request.user,
            note=request.POST.get("note", ""),
        )
    except CheckoutError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Qaytarildi: {_money(amount)}.")
    return redirect("sale_detail", pk=sale.pk)


# ---------------------------------------------------------------------------
# Mijozlar
# ---------------------------------------------------------------------------

@login_required
@staff_required
def customer_list(request):
    company = _company_of(request.user)
    customers = Customer.objects.filter(company=company)

    query = request.GET.get("q", "").strip()
    if query:
        customers = customers.filter(
            Q(full_name__icontains=query) | Q(phone__icontains=query)
            | Q(address__icontains=query)
        )

    only_debt = request.GET.get("qarz") == "1"
    if only_debt:
        customers = customers.filter(debt__gt=0)

    page = Paginator(customers, 30).get_page(request.GET.get("page"))

    return render(request, "core/customer_list.html", {
        "page_title": "Mijozlar",
        "currency": settings.POS_CURRENCY,
        "page_obj": page,
        "customers": page.object_list,
        "total": customers.count(),
        "query": query,
        "only_debt": only_debt,
        "debt": reporting.debt_summary(company),
    })


@login_required
@staff_required
def customer_edit(request, pk=None):
    company = _company_of(request.user)
    instance = get_object_or_404(Customer, pk=pk, company=company) if pk else None

    if request.method == "POST":
        form = CustomerForm(request.POST, instance=instance)
        if form.is_valid():
            customer = form.save(commit=False)
            customer.company = company
            customer.save()
            messages.success(request, f"{customer.full_name} saqlandi.")
            return redirect("customer_detail", pk=customer.pk)
    else:
        form = CustomerForm(instance=instance)

    return render(request, "core/customer_form.html", {
        "page_title": "Mijozni tahrirlash" if instance else "Yangi mijoz",
        "form": form,
        "instance": instance,
    })


@login_required
@staff_required
def customer_detail(request, pk):
    company = _company_of(request.user)
    customer = get_object_or_404(Customer, pk=pk, company=company)
    sales = customer.sales.select_related("branch", "cashier")

    return render(request, "core/customer_detail.html", {
        "page_title": customer.full_name,
        "currency": settings.POS_CURRENCY,
        "customer": customer,
        "sales": sales[:30],
        "payments": customer.payments.select_related("user", "branch")[:20],
        "stats": reporting.summary(sales),
        "payment_form": CustomerPaymentForm(customer=customer),
    })


@login_required
@staff_required
@require_POST
def customer_pay(request, pk):
    """Mijozdan qarz to'lovini qabul qiladi."""
    company = _company_of(request.user)
    customer = get_object_or_404(Customer, pk=pk, company=company)
    form = CustomerPaymentForm(request.POST, customer=customer)

    if form.is_valid():
        try:
            pay_debt(
                customer=customer,
                amount=form.cleaned_data["amount"],
                user=request.user,
                branch=request.user.active_branch,
                note=form.cleaned_data["note"],
            )
        except CheckoutError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request, f"To'lov qabul qilindi. Qolgan qarz: {_money(customer.debt)}.",
            )
    else:
        messages.error(request, form.errors.as_text())
    return redirect("customer_detail", pk=customer.pk)


# ---------------------------------------------------------------------------
# Hisobotlar
# ---------------------------------------------------------------------------

@login_required
@staff_required
def reports(request):
    branches = request.user.visible_branches()
    since, until = reporting.day_bounds(request, default_days=29)

    sales = reporting.sales_of(branches, since, until)
    branch_id = request.GET.get("branch", "")
    if branch_id.isdigit():
        sales = sales.filter(branch_id=int(branch_id))

    days = (until - since).days + 1
    stats = reporting.summary(sales)
    payments = reporting.by_payment(sales)
    alerts = reporting.stock_alerts(branches)

    data = {
        **reporting.daily_series(sales, days=min(days, 62), until=until),
        "payments": payments,
        "top_products": reporting.top_products(sales, limit=10),
    }

    return render(request, "core/reports.html", {
        "page_title": "Hisobotlar",
        "currency": settings.POS_CURRENCY,
        "since": since,
        "until": until,
        "days": days,
        "branch_id": branch_id,
        "branches": branches,
        "stats": stats,
        "data": data,
        "payments": payments,
        "payments_total": sum(p["value"] for p in payments),
        "top_products": data["top_products"],
        "cashiers": reporting.by_cashier(sales),
        "branch_rows": reporting.by_branch(sales),
        "alerts": alerts,
        "debt": reporting.debt_summary(_company_of(request.user)),
        "chart_rows": list(zip(data["days"], data["revenue"], data["profit"])),
    })


# ---------------------------------------------------------------------------
# Xodimlar
# ---------------------------------------------------------------------------

@login_required
@manager_required
def staff_list(request):
    staff = User.objects.filter(company=request.user.company).prefetch_related("branches")
    query = request.GET.get("q", "").strip()
    if query:
        staff = staff.filter(
            Q(username__icontains=query) | Q(first_name__icontains=query)
            | Q(last_name__icontains=query) | Q(phone__icontains=query)
        )
    return render(request, "core/staff_list.html", {
        "page_title": "Xodimlar",
        "staff": staff,
        "query": query,
        "roles": User.Role.choices,
    })


@login_required
@manager_required
def staff_edit(request, pk=None):
    instance = get_object_or_404(User, pk=pk, company=request.user.company) if pk else None

    if request.method == "POST":
        form = StaffForm(request.POST, instance=instance, company=request.user.company)
        if form.is_valid():
            user = form.save()
            messages.success(request, f"{user} saqlandi.")
            return redirect("staff_list")
    else:
        form = StaffForm(instance=instance, company=request.user.company)

    return render(request, "core/staff_form.html", {
        "page_title": "Xodimni tahrirlash" if instance else "Yangi xodim",
        "form": form,
        "instance": instance,
    })


# ---------------------------------------------------------------------------
# Sozlamalar
# ---------------------------------------------------------------------------

@login_required
def switch_branch(request):
    """Xodim faol filialini almashtiradi (2+ filial biriktirilgan bo'lsa)."""
    if request.method != "POST":
        return redirect("dashboard")

    form = BranchSwitchForm(request.POST, user=request.user)
    if form.is_valid():
        request.user.branch = form.cleaned_data["branch"]
        request.user.save(update_fields=["branch"])
        messages.success(request, f"Faol filial: {request.user.branch.name}")
    else:
        messages.error(request, "Filialni tanlab bo'lmadi.")
    return _back(request, "branch")


@login_required
@manager_required
def branch_save(request, pk=None):
    """Sozlamalardan filial qo'shish yoki tahrirlash."""
    if request.method != "POST":
        return redirect("dashboard")

    instance = get_object_or_404(Branch, pk=pk, company=request.user.company) if pk else None
    form = BranchForm(request.POST, instance=instance)
    if form.is_valid():
        branch = form.save(commit=False)
        branch.company = request.user.company
        branch.save()
        messages.success(request, f"Filial saqlandi: {branch.name}")
    else:
        messages.error(request, "Filial saqlanmadi: " + form.errors.as_text())
    return _back(request, "branches")


@login_required
@manager_required
def branch_toggle(request, pk):
    """Filialni faollashtirish / nofaollashtirish."""
    if request.method != "POST":
        return redirect("dashboard")

    branch = get_object_or_404(Branch, pk=pk, company=request.user.company)
    branch.is_active = not branch.is_active
    branch.save(update_fields=["is_active"])
    messages.success(
        request,
        f"{branch.name} — {'faollashtirildi' if branch.is_active else 'nofaollashtirildi'}.",
    )
    return _back(request, "branches")


@login_required
@manager_required
def company_save(request):
    if request.method != "POST":
        return redirect("dashboard")

    form = CompanySettingsForm(request.POST, instance=request.user.company)
    if form.is_valid():
        form.save()
        messages.success(request, "Kompaniya sozlamalari saqlandi.")
    else:
        messages.error(request, "Sozlamalar saqlanmadi: " + form.errors.as_text())
    return _back(request, "company")
