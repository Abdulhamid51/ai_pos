import json
import time
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.humanize.templatetags.humanize import intcomma
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import ai, documents, reporting, services
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
    SupplierForm,
    VariantFormSet,
    WaybillHeaderForm,
    WaybillItemFormSet,
    WaybillSaleForm,
    WaybillUploadForm,
)
from .models import (
    TRADE_TYPE_FIELDS,
    Branch,
    Conversation,
    Color,
    Customer,
    Message,
    PaymentMethod,
    Product,
    Sale,
    StockMovement,
    Supplier,
    User,
    Waybill,
    apply_stock,
)
from .services import CheckoutError, checkout, pay_debt, refund

# Direktor (yoki superuser) uchun cheklov.
manager_required = user_passes_test(lambda u: u.is_authenticated and u.can_manage())

# Kassa va qaytarish — sotish huquqi bor rollar uchun.
seller_required = user_passes_test(lambda u: u.is_authenticated and u.can_sell())

# Ichki sahifalar — mijoz rolidagi foydalanuvchiga ochilmaydi.
staff_required = user_passes_test(lambda u: u.is_authenticated and u.is_xodim)

# Tovar qabul qilish va ta'minotchilar — direktor va ta'minotchi uchun.
receiver_required = user_passes_test(lambda u: u.is_authenticated and u.can_receive())


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
# AI yordamchi
# ---------------------------------------------------------------------------

# Ro'yxatda ko'rsatiladigan suhbatlar soni.
CHAT_LIST_LIMIT = 50


def _title_of(message):
    """Suhbat sarlavhasi — birinchi savoldan olinadi."""
    title = " ".join(message.split())
    return title[:70] + "…" if len(title) > 70 else title


def _message_payload(message):
    """Xabarni brauzerga beriladigan ko'rinishga keltiradi."""
    data = {
        "role": {1: "user", 2: "ai", 3: "error"}[message.role],
        "text": message.text,
        "sources": message.sources or [],
        "tables": message.tables or [],
    }
    if message.role == Message.Role.AI:
        data["stats"] = {
            "tokens": message.total_tokens,
            "input": message.input_tokens,
            "output": message.output_tokens,
            "seconds": round(message.seconds, 1),
            "tools": message.tool_calls,
        }
    return data


@login_required
@staff_required
def chat(request, pk=None):
    """AI yordamchi. `pk` berilsa — saqlangan suhbat ochiladi."""
    conversations = Conversation.objects.filter(user=request.user)[:CHAT_LIST_LIMIT]

    conversation = None
    history = []
    if pk:
        conversation = get_object_or_404(Conversation, pk=pk, user=request.user)
        history = [_message_payload(m) for m in conversation.messages.all()]

    company = _company_of(request.user)
    return render(request, "core/chat.html", {
        "page_title": "AI yordamchi",
        "store_name": company.name if company else settings.POS_STORE_NAME,
        "branch": request.user.active_branch,
        "model_name": settings.GEMINI_MODEL,
        "ai_ready": ai.is_configured(),
        "conversations": conversations,
        "conversation": conversation,
        "history": history,   # json_script shablonda o'zi JSON qiladi
    })


# Chatdagi buyruqlar: fayl bilan birga yuboriladi.
CHAT_COMMANDS = {"/qabul": Waybill.Kind.QABUL, "/sotuv": Waybill.Kind.SOTUV}


def _conversation_for(request, payload, title):
    conversation_id = payload.get("conversation")
    if conversation_id:
        return get_object_or_404(Conversation, pk=conversation_id, user=request.user)
    return Conversation.objects.create(user=request.user, title=_title_of(title))


def _chat_command(request, payload, command, text, uploaded):
    """`/qabul` va `/sotuv`: faylni o'qib, qoralama nakladnoy yaratadi.

    Bazaga hech narsa yozilmaydi — javobda ko'rib chiqish sahifasiga havola
    beriladi, tasdiqlash o'sha yerda.
    """
    kind = CHAT_COMMANDS.get(command)
    label = uploaded.name if uploaded else ""
    conversation = _conversation_for(request, payload, text or f"Nakladnoy: {label}")
    base = {
        "conversation": conversation.pk,
        "title": conversation.title,
        "url": reverse("chat_detail", args=[conversation.pk]),
    }

    def reply(message_text, role=Message.Role.AI, **extra):
        message = Message.objects.create(
            conversation=conversation, role=role, text=message_text, **extra
        )
        conversation.save(update_fields=["updated_at"])
        data = _message_payload(message)
        if role == Message.Role.XATO:
            return JsonResponse({**base, "error": message_text}, status=400)
        return JsonResponse({**base, **data, "reply": message_text})

    user_text = f"{text or command} 📎 {label}".strip() if uploaded else text
    Message.objects.create(conversation=conversation, role=Message.Role.FOYDALANUVCHI, text=user_text)

    if kind is None:
        return reply(
            "Fayl bilan birga buyruq yozing: /qabul — tovar qabul qilish, "
            "/sotuv — nakladnoy bo'yicha sotish.",
            role=Message.Role.XATO,
        )
    if uploaded is None:
        return reply(
            f"{command} uchun nakladnoy faylini biriktiring (rasm, PDF, Excel yoki CSV).",
            role=Message.Role.XATO,
        )

    started = time.monotonic()
    try:
        waybill, stats, error = start_waybill(request.user, kind, uploaded)
    except (services.WaybillError, documents.DocumentError) as error:
        return reply(str(error), role=Message.Role.XATO)

    link = reverse("waybill_detail", args=[waybill.pk])
    action = "qabul" if kind == Waybill.Kind.QABUL else "sotuv"

    if error:
        text_out = (
            f"Faylni o'qib bo'lmadi: {error}\n"
            f"Qoralama saqlandi — qatorlarni qo'lda kiritishingiz mumkin."
        )
    else:
        lines = [f"Nakladnoy o'qildi: {stats['items']} ta qator, {stats['matched']} tasi bazadagi mahsulotga moslandi."]
        if waybill.number:
            lines.append(f"Raqami: {waybill.number}" + (f", sanasi: {waybill.doc_date:%d.%m.%Y}" if waybill.doc_date else ""))
        if waybill.supplier:
            lines.append(f"Ta'minotchi: {waybill.supplier.name}")
        elif waybill.supplier_name and kind == Waybill.Kind.QABUL:
            lines.append(f"Hujjatdagi ta'minotchi ro'yxatda yo'q: {waybill.supplier_name}")
        unmatched = stats["items"] - stats["matched"]
        if unmatched:
            lines.append(
                f"{unmatched} ta qator bazada topilmadi — "
                + ("tasdiqlashda yangi mahsulot bo'ladi, sotuv narxini kiriting." if kind == Waybill.Kind.QABUL
                   else "sotishdan oldin mahsulotni tanlang.")
            )
        for warning in stats["warnings"]:
            lines.append(f"Diqqat: {warning}")
        lines.append(f"Hali hech narsa o'zgarmadi — {action}ni tekshirib tasdiqlang.")
        text_out = "\n".join(lines)

    items = list(waybill.items.select_related("product"))
    table = {
        "sarlavha": f"Nakladnoy №{waybill.number or waybill.pk} · qoralama",
        "ustunlar": ["Hujjatdagi nomi", "Bazadagi mahsulot", "Soni", "Narxi", "Jami"],
        "qatorlar": [
            [item.name, str(item.product) if item.product else "— topilmadi —",
             float(item.quantity), float(item.price), float(item.line_total)]
            for item in items
        ],
        "jami": len(items),
        "havola": link,
        "havola_matni": "Ko'rib chiqish va tasdiqlash →",
    }

    return reply(
        text_out,
        sources=[f"Nakladnoy: {label}"],
        tables=[table] if items else [],
        input_tokens=waybill.parse_tokens,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


@login_required
@staff_required
@require_POST
def chat_send(request):
    """Savolni modelga uzatadi, savol va javobni bazaga yozadi.

    Fayl biriktirilgan bo'lsa so'rov multipart ko'rinishida keladi va
    `/qabul` yoki `/sotuv` buyrug'i nakladnoy sifatida qayta ishlanadi.
    """
    if request.content_type.startswith("multipart/"):
        payload = request.POST
        uploaded = request.FILES.get("file")
    else:
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            return JsonResponse({"error": "So'rov formati noto'g'ri."}, status=400)
        uploaded = None

    text = (payload.get("message") or "").strip()
    command = text.split()[0].lower() if text.startswith("/") else ""
    if command in CHAT_COMMANDS or uploaded:
        return _chat_command(request, payload, command, text, uploaded)

    if not text:
        return JsonResponse({"error": "Xabar bo'sh."}, status=400)
    if len(text) > 2000:
        return JsonResponse({"error": "Xabar juda uzun — 2000 belgidan oshmasin."}, status=400)

    # Suhbat birinchi savol yuborilganda yaratiladi — bo'sh suhbat qolmaydi.
    conversation = _conversation_for(request, payload, text)

    Message.objects.create(
        conversation=conversation, role=Message.Role.FOYDALANUVCHI, text=text
    )

    base = {
        "conversation": conversation.pk,
        "title": conversation.title,
        "url": reverse("chat_detail", args=[conversation.pk]),
    }

    try:
        result = ai.chat_reply(
            text, request.user, previous_id=conversation.last_interaction_id or None
        )
    except ai.AIError as error:
        # Xato ham tarixda qoladi — keyin nima bo'lganini ko'rish mumkin.
        Message.objects.create(
            conversation=conversation, role=Message.Role.XATO, text=str(error)
        )
        conversation.save(update_fields=["updated_at"])
        return JsonResponse({**base, "error": str(error)}, status=503)

    conversation.last_interaction_id = result.interaction_id
    conversation.save(update_fields=["last_interaction_id", "updated_at"])

    message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.AI,
        text=result.text,
        sources=result.sources,
        tables=result.tables,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        elapsed_ms=result.elapsed_ms,
        tool_calls=result.tool_calls,
    )

    return JsonResponse({**base, **_message_payload(message), "reply": result.text})


@login_required
@staff_required
@require_POST
def chat_delete(request, pk):
    """Suhbatni o'chiradi. Xabarlar ham birga ketadi (CASCADE)."""
    conversation = get_object_or_404(Conversation, pk=pk, user=request.user)
    conversation.delete()
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"ok": True})
    return redirect("chat")


# ---------------------------------------------------------------------------
# Ta'minotchilar
# ---------------------------------------------------------------------------

@login_required
@receiver_required
def supplier_list(request):
    company = _company_of(request.user)
    # `annotate` dan keyin Meta.ordering kafolatlanmaydi — tartib aniq beriladi,
    # aks holda sahifalashda qatorlar sahifalar orasida takrorlanishi mumkin.
    suppliers = Supplier.objects.filter(company=company).annotate(
        waybill_count=Count("waybills")
    ).order_by("name")

    query = request.GET.get("q", "").strip()
    if query:
        suppliers = suppliers.filter(
            Q(name__icontains=query) | Q(phone__icontains=query) | Q(tin__icontains=query)
        )

    page = Paginator(suppliers, 30).get_page(request.GET.get("page"))
    total_debt = Supplier.objects.filter(company=company).aggregate(total=Sum("debt"))["total"]

    return render(request, "core/supplier_list.html", {
        "page_title": "Ta'minotchilar",
        "currency": settings.POS_CURRENCY,
        "page_obj": page,
        "suppliers": page.object_list,
        "total": suppliers.count(),
        "total_debt": total_debt or 0,
        "query": query,
    })


@login_required
@receiver_required
def supplier_edit(request, pk=None):
    company = _company_of(request.user)
    instance = get_object_or_404(Supplier, pk=pk, company=company) if pk else None

    if request.method == "POST":
        form = SupplierForm(request.POST, instance=instance, company=company)
        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.company = company
            supplier.save()
            messages.success(request, f"{supplier.name} saqlandi.")
            # Nakladnoydan kelgan bo'lsa — o'sha yerga qaytamiz.
            back = request.POST.get("qaytish", "")
            if back.startswith("/qabul/"):
                return redirect(back)
            return redirect("supplier_detail", pk=supplier.pk)
    else:
        form = SupplierForm(
            instance=instance, company=company,
            initial={"name": request.GET.get("nom", "")[:150]} if not instance else None,
        )

    return render(request, "core/supplier_form.html", {
        "page_title": "Ta'minotchini tahrirlash" if instance else "Yangi ta'minotchi",
        "form": form,
        "instance": instance,
        "back": request.GET.get("qaytish") or request.POST.get("qaytish", ""),
    })


@login_required
@receiver_required
def supplier_detail(request, pk):
    company = _company_of(request.user)
    supplier = get_object_or_404(Supplier, pk=pk, company=company)
    waybills = supplier.waybills.select_related("branch").prefetch_related("items")[:50]
    return render(request, "core/supplier_detail.html", {
        "page_title": supplier.name,
        "currency": settings.POS_CURRENCY,
        "supplier": supplier,
        "waybills": waybills,
    })


# ---------------------------------------------------------------------------
# Nakladnoylar
# ---------------------------------------------------------------------------

LEGAL_FORMS = ("mchj", "ooo", "ооо", "xk", "ик", "ип", "ao", "ат", "llc", "ltd")


def _org_key(name):
    """Tashkilot nomini solishtirish uchun: qo'shtirnoq va huquqiy shakl olib tashlanadi."""
    cleaned = "".join(ch for ch in (name or "").lower() if ch.isalnum() or ch.isspace())
    words = [w for w in cleaned.split() if w not in LEGAL_FORMS]
    return " ".join(words)


def _guess_supplier(company, name):
    """Hujjatdagi nom bo'yicha mavjud ta'minotchini topadi. Topilmasa — None."""
    key = _org_key(name)
    if not key:
        return None
    for supplier in Supplier.objects.filter(company=company, is_active=True):
        other = _org_key(supplier.name)
        if other and (other == key or other in key or key in other):
            return supplier
    return None


def _can_handle(user, kind):
    """Qabulni ta'minotchi/direktor, sotuvni sotish huquqi borlar bajaradi."""
    return user.can_receive() if kind == Waybill.Kind.QABUL else user.can_sell()


def start_waybill(user, kind, uploaded):
    """Faylni saqlaydi va AI bilan o'qiydi. Sahifa ham, chat ham shuni chaqiradi.

    Qaytadi: (waybill, stats, xato_matni). O'qishda xato bo'lsa ham qoralama
    saqlanadi — foydalanuvchi qatorlarni qo'lda kiritishi mumkin.
    """
    branch = user.active_branch
    if branch is None:
        raise services.WaybillError("Faol filial tanlanmagan.")
    if not _can_handle(user, kind):
        raise services.WaybillError("Bu amal uchun huquqingiz yo'q.")

    documents.check_file(uploaded)
    waybill = Waybill.objects.create(
        branch=branch, kind=kind, created_by=user,
        file=uploaded, file_name=uploaded.name[:255],
    )

    try:
        stats = documents.parse_waybill(waybill)
    except (documents.DocumentError, ai.AIError) as error:
        return waybill, None, str(error)

    if kind == Waybill.Kind.QABUL and waybill.supplier_name:
        supplier = _guess_supplier(branch.company, waybill.supplier_name)
        if supplier:
            waybill.supplier = supplier
            waybill.save(update_fields=["supplier"])

    return waybill, stats, None


@login_required
@staff_required
def waybill_list(request):
    branches = request.user.visible_branches()
    waybills = (
        Waybill.objects.filter(branch__in=branches)
        .select_related("branch", "supplier", "customer", "created_by")
        .prefetch_related("items")
    )

    kind = request.GET.get("tur", "qabul")
    if kind == "sotuv":
        waybills = waybills.filter(kind=Waybill.Kind.SOTUV)
    elif kind == "qabul":
        waybills = waybills.filter(kind=Waybill.Kind.QABUL)

    status = request.GET.get("holat", "")
    if status.isdigit():
        waybills = waybills.filter(status=int(status))

    page = Paginator(waybills, 30).get_page(request.GET.get("page"))
    initial_kind = Waybill.Kind.SOTUV if kind == "sotuv" else Waybill.Kind.QABUL

    return render(request, "core/waybill_list.html", {
        "page_title": "Qabul va nakladnoylar",
        "currency": settings.POS_CURRENCY,
        "page_obj": page,
        "waybills": page.object_list,
        "kind": kind,
        "status": status,
        "statuses": Waybill.Status.choices,
        "upload_form": WaybillUploadForm(initial={"kind": initial_kind}),
        "can_receive": request.user.can_receive(),
    })


@login_required
@staff_required
@require_POST
def waybill_upload(request):
    form = WaybillUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Fayl tanlanmagan.")
        return redirect("waybill_list")

    try:
        waybill, stats, error = start_waybill(
            request.user, form.cleaned_data["kind"], form.cleaned_data["file"]
        )
    except (services.WaybillError, documents.DocumentError) as error:
        messages.error(request, str(error))
        return redirect("waybill_list")

    if error:
        messages.warning(request, f"Faylni o'qib bo'lmadi: {error} Qatorlarni qo'lda kiriting.")
    else:
        messages.success(
            request,
            f"{stats['items']} ta qator o'qildi, {stats['matched']} tasi mahsulotga moslandi. "
            f"Tekshirib, tasdiqlang.",
        )
        for warning in stats["warnings"]:
            messages.warning(request, warning)
    return redirect("waybill_detail", pk=waybill.pk)


@login_required
@staff_required
def waybill_detail(request, pk):
    waybill = get_object_or_404(
        Waybill.objects.select_related("branch", "supplier", "customer", "sale"),
        pk=pk, branch__in=request.user.visible_branches(),
    )
    if not _can_handle(request.user, waybill.kind):
        messages.error(request, "Bu nakladnoy bilan ishlash huquqingiz yo'q.")
        return redirect("waybill_list")

    company = waybill.branch.company
    editable = waybill.is_draft

    header = WaybillHeaderForm(
        request.POST or None, instance=waybill, company=company
    ) if editable else None
    formset = WaybillItemFormSet(
        request.POST or None, instance=waybill, prefix="items",
        form_kwargs={"branch": waybill.branch},
    ) if editable else None
    sale_form = WaybillSaleForm(request.POST or None) if waybill.kind == Waybill.Kind.SOTUV else None

    if request.method == "POST" and editable:
        action = request.POST.get("action", "save")

        if action == "cancel":
            waybill.status = Waybill.Status.BEKOR
            waybill.save(update_fields=["status"])
            messages.info(request, "Nakladnoy bekor qilindi. Qoldiqqa tegilmadi.")
            return redirect("waybill_list")

        if header.is_valid() and formset.is_valid():
            header.save()
            formset.save()

            if action != "confirm":
                messages.success(request, "O'zgarishlar saqlandi.")
                return redirect("waybill_detail", pk=waybill.pk)

            try:
                if waybill.kind == Waybill.Kind.QABUL:
                    services.confirm_receipt(waybill, request.user)
                    messages.success(request, "Qabul tasdiqlandi — qoldiq yangilandi.")
                    return redirect("waybill_detail", pk=waybill.pk)

                if sale_form.is_valid():
                    waybill.refresh_from_db()
                    confirmed = services.confirm_sale(
                        waybill, request.user,
                        payment_method=sale_form.cleaned_data["payment_method"],
                        customer=waybill.customer,
                    )
                    messages.success(request, f"Sotuv tasdiqlandi — chek №{confirmed.sale.number}.")
                    return redirect("sale_detail", pk=confirmed.sale.pk)
            except services.WaybillError as error:
                messages.error(request, str(error))
        else:
            messages.error(request, "Formada xatolar bor — belgilangan maydonlarni tekshiring.")

    items = list(waybill.items.select_related("product"))
    return render(request, "core/waybill_detail.html", {
        "page_title": str(waybill),
        "currency": settings.POS_CURRENCY,
        "waybill": waybill,
        "editable": editable,
        "header": header,
        "formset": formset,
        "sale_form": sale_form,
        "items": items,
        "total": sum((item.line_total for item in items), Decimal("0")),
        "unmatched": sum(1 for item in items if item.product_id is None),
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
