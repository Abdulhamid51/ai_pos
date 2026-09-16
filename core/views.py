import random
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    BranchForm,
    BranchSwitchForm,
    CompanySettingsForm,
    ProductBaseForm,
    StaffForm,
    VariantFormSet,
)
from .models import TRADE_TYPE_FIELDS, Branch, Color, Product, TradeType, User

# Direktor (yoki superuser) uchun cheklov.
manager_required = user_passes_test(lambda u: u.is_authenticated and u.can_manage())


def _back(request, panel=""):
    """Sozlamalar modalidan kelgan formadan keyin o'sha sahifaga qaytarish."""
    target = request.POST.get("next") or "/"
    return redirect(f"{target}#settings/{panel}" if panel else target)


# ---------------------------------------------------------------------------
# Boshqaruv paneli
# ---------------------------------------------------------------------------

FAKE_SEED = 2026


def _fake_sales_data():
    rnd = random.Random(FAKE_SEED)
    today = timezone.localdate()

    days, revenue, profit = [], [], []
    for offset in range(13, -1, -1):
        day = today - timedelta(days=offset)
        base = 4_600_000 if day.weekday() >= 5 else 3_200_000
        amount = base + rnd.randint(-600_000, 900_000)
        days.append(day.strftime("%d.%m"))
        revenue.append(amount)
        profit.append(int(amount * rnd.uniform(0.18, 0.27)))

    hours = [f"{h:02d}:00" for h in range(9, 22)]
    hour_shape = [0.4, 0.7, 1.0, 1.3, 1.1, 0.8, 0.9, 1.1, 1.4, 1.6, 1.2, 0.8, 0.5]
    hourly = [int(260_000 * k + rnd.randint(-40_000, 60_000)) for k in hour_shape]

    payments = [
        {"label": "Naqd", "value": 12_400_000},
        {"label": "Plastik karta", "value": 21_900_000},
        {"label": "O'tkazma", "value": 5_300_000},
    ]
    top_products = [
        {"name": "Coca-Cola 1L", "qty": 184, "total": 2_208_000},
        {"name": "Non (oddiy)", "qty": 176, "total": 704_000},
        {"name": "Sut 1L", "qty": 142, "total": 1_704_000},
        {"name": "Tuxum (10 dona)", "qty": 118, "total": 2_596_000},
        {"name": "Shakar 1kg", "qty": 97, "total": 1_358_000},
    ]
    checks_today = rnd.randint(120, 180)

    return {
        "days": days, "revenue": revenue, "profit": profit,
        "hours": hours, "hourly": hourly,
        "payments": payments, "top_products": top_products,
        "kpi": {
            "revenue_today": revenue[-1],
            "revenue_change": round((revenue[-1] / revenue[-2] - 1) * 100, 1),
            "profit_today": profit[-1],
            "checks_today": checks_today,
            "avg_check": revenue[-1] // checks_today,
            "margin_pct": round(profit[-1] / revenue[-1] * 100, 1),
            "low_stock": 7,
        },
    }


@login_required
def dashboard(request):
    data = _fake_sales_data()
    return render(request, "core/dashboard.html", {
        "page_title": "Boshqaruv paneli",
        "store_name": settings.POS_STORE_NAME,
        "currency": settings.POS_CURRENCY,
        "data": data,
        "kpi": data["kpi"],
        "chart_rows": list(zip(data["days"], data["revenue"], data["profit"])),
        "payments_total": sum(p["value"] for p in data["payments"]),
    })


# ---------------------------------------------------------------------------
# Mahsulotlar
# ---------------------------------------------------------------------------

@login_required
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
