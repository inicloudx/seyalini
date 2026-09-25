from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from dashboard import pwa

admin.site.site_header = "Seyalini admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("manifest.webmanifest", pwa.manifest),
    path("sw.js", pwa.service_worker),
    path("", include("dashboard.urls")),
]

if settings.DEBUG:  # serve generated videos locally
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
