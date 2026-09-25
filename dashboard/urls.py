from django.urls import path

from . import product_views, settings_views, views

app_name = "dashboard"
urlpatterns = [
    path("", views.home, name="home"),
    path("tasks/<int:task_id>/decide/", views.decide, name="decide"),
    path("agents/marketing/run/", views.run_marketing, name="run_marketing"),
    path("tenant/switch/", views.switch_tenant, name="switch_tenant"),
    path("running/", views.running, name="running"),
    path("shorts/<int:task_id>/action/", views.flow_action, name="flow_action"),
    path("videos/", views.videos, name="videos"),
    path("videos/<int:task_id>/delete/", views.video_delete, name="video_delete"),
    path("videos/cleanup/", views.videos_cleanup, name="videos_cleanup"),
    path("videos/<int:task_id>/publish/", views.video_publish, name="video_publish"),
    path("advanced/", views.advanced, name="advanced"),
    path("settings/", settings_views.settings_home, name="settings"),
    path("settings/youtube/connect/", settings_views.youtube_connect, name="youtube_connect"),
    path("settings/youtube/callback/", settings_views.youtube_callback, name="youtube_callback"),
    path("settings/youtube/disconnect/", settings_views.youtube_disconnect, name="youtube_disconnect"),
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
