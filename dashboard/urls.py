from django.urls import path

from . import product_views, settings_views, views

app_name = "dashboard"
urlpatterns = [
    path("", views.home, name="home"),
    path("tasks/<int:task_id>/decide/", views.decide, name="decide"),
    path("agents/marketing/run/", views.run_marketing, name="run_marketing"),
    path("tenant/switch/", views.switch_tenant, name="switch_tenant"),
    path("running/", views.running, name="running"),
    path("settings/", settings_views.settings_home, name="settings"),
    path("platform/", settings_views.platform, name="platform"),
    path("platform/open/<slug:slug>/", settings_views.platform_open, name="platform_open"),
    path("signup/", settings_views.signup, name="signup"),
    path("products/", product_views.product_list, name="products"),
    path("products/new/", product_views.product_new, name="product_new"),
    path("products/<slug:slug>/", product_views.product_detail, name="product"),
    path("products/<slug:slug>/improve/", product_views.product_improve, name="product_improve"),
    path("products/<slug:slug>/discard/", product_views.product_discard_draft, name="product_discard"),
    path("products/<slug:slug>/settings/", product_views.product_settings, name="product_settings"),
    path("products/<slug:slug>/archive/", product_views.product_archive, name="product_archive"),
    path("products/<slug:slug>/restore/", product_views.product_restore, name="product_restore"),
]
