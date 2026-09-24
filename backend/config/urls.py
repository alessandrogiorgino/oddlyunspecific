from django.conf import settings
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path, re_path
from django_otp.admin import OTPAdminSite

from apps.blog.sitemaps import PostSitemap, StaticSitemap
from apps.writer.forms import ConsoleAdminAuthenticationForm
from config import views

# Swap the default admin site for the OTP one: a valid password alone will not
# open it, a TOTP code is required on the same form. Done here rather than with
# a custom AdminSite so third-party admin registrations keep working.
admin.site.__class__ = OTPAdminSite
# ...and with the same single-step form as the console, so the admin does not
# need the two-round "now pick a device" dance django-otp 1.7 defaults to.
admin.site.login_form = ConsoleAdminAuthenticationForm
admin.site.site_header = f"{settings.SITE_NAME} admin"
admin.site.site_title = settings.SITE_NAME
admin.site.index_title = "content"

sitemaps = {"posts": PostSitemap, "static": StaticSitemap}

urlpatterns = [
    # Not /admin/. The real path comes from DJANGO_ADMIN_PATH and is never
    # printed in robots.txt, the sitemap or any template.
    path(f"{settings.ADMIN_PATH}/", admin.site.urls),
    path("healthz/", views.healthz, name="healthz"),
    path("robots.txt", views.robots_txt, name="robots"),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),
    re_path(r"^media/(?P<path>.+)$", views.serve_media, name="media"),
    path("write/", include("apps.writer.urls")),
    path("private/", include("apps.journal.urls")),
    path("", include("apps.blog.urls")),
]

handler404 = "apps.blog.views.not_found"
handler500 = "apps.blog.views.server_error"
