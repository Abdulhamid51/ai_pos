from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    path("kassa/", views.pos, name="pos"),
    path("kassa/qidiruv/", views.pos_search, name="pos_search"),
    path("kassa/yakunlash/", views.pos_checkout, name="pos_checkout"),

    path("sotuvlar/", views.sale_list, name="sale_list"),
    path("sotuvlar/<int:pk>/", views.sale_detail, name="sale_detail"),
    path("sotuvlar/<int:pk>/chek/", views.sale_receipt, name="sale_receipt"),
    path("sotuvlar/<int:pk>/qaytarish/", views.sale_refund, name="sale_refund"),

    path("mijozlar/", views.customer_list, name="customer_list"),
    path("mijozlar/yangi/", views.customer_edit, name="customer_create"),
    path("mijozlar/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("mijozlar/<int:pk>/tahrirlash/", views.customer_edit, name="customer_edit"),
    path("mijozlar/<int:pk>/tolov/", views.customer_pay, name="customer_pay"),

    path("mahsulotlar/", views.product_list, name="product_list"),
    path("mahsulotlar/yangi/", views.product_create, name="product_create"),
    path("mahsulotlar/<int:pk>/", views.product_edit, name="product_edit"),
    path("mahsulotlar/<int:pk>/ochirish/", views.product_delete, name="product_delete"),

    path("hisobotlar/", views.reports, name="reports"),

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
