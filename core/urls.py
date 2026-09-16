from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    path("mahsulotlar/", views.product_list, name="product_list"),
    path("mahsulotlar/yangi/", views.product_create, name="product_create"),

    path("xodimlar/", views.staff_list, name="staff_list"),
    path("xodimlar/yangi/", views.staff_edit, name="staff_create"),
    path("xodimlar/<int:pk>/", views.staff_edit, name="staff_edit"),

    path("sozlamalar/filial-tanlash/", views.switch_branch, name="switch_branch"),
    path("sozlamalar/filial/", views.branch_save, name="branch_create"),
    path("sozlamalar/filial/<int:pk>/", views.branch_save, name="branch_edit"),
    path("sozlamalar/filial/<int:pk>/holat/", views.branch_toggle, name="branch_toggle"),
    path("sozlamalar/kompaniya/", views.company_save, name="company_save"),

    path("kirish/", auth_views.LoginView.as_view(template_name="core/login.html"), name="login"),
    path("chiqish/", auth_views.LogoutView.as_view(), name="logout"),
]
