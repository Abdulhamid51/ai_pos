"""Har bir sahifaga kerak bo'ladigan umumiy kontekst.

Sozlamalar modali barcha sahifalarda turgani uchun, unga kerakli
ma'lumotlar ham shu yerdan beriladi.
"""

from .models import Branch, TradeType


def pos_context(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    from .forms import CompanySettingsForm   # aylanma importdan qochish uchun shu yerda

    active_branch = user.active_branch
    can_manage = user.can_manage()
    # Direktor uchun rang mavzusi o'zgarmaydi — u barcha filiallar bilan ishlaydi.
    trade_type = TradeType.UNIVERSAL if can_manage else user.trade_type
    company = user.company or (active_branch.company if active_branch else None)

    return {
        "active_branch": active_branch,
        "trade_type": int(trade_type),
        "trade_types": TradeType.choices,
        "user_branches": user.branches.filter(is_active=True).select_related("company"),
        "can_switch_branch": user.can_switch_branch,
        "can_manage": can_manage,
        "company": company,
        "managed_branches": (
            Branch.objects.filter(company=company).select_related("company")
            if can_manage and company else Branch.objects.none()
        ),
        "company_form": CompanySettingsForm(instance=company) if can_manage and company else None,
    }
