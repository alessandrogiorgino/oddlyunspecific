from django.conf import settings
from django.contrib.auth import views as auth_views
from django.shortcuts import render
from django.views.decorators.http import require_safe

from apps.accounts.decorators import staff_otp_required

from .forms import ConsoleAuthenticationForm


class ConsoleLoginView(auth_views.LoginView):
    """
    One form, three fields: username, password, second factor.

    The password is checked first and the token second, so a correct password
    with a wrong code is still a failed attempt and still feeds django-axes.
    There is no "logged in but not verified" state to get stuck in.
    """

    template_name = "writer/login.html"
    authentication_form = ConsoleAuthenticationForm
    # Leave this False: redirect_authenticated_user=True is a documented
    # user-enumeration hole. Django's own LoginView.dispatch already carries
    # sensitive_post_parameters(), csrf_protect and never_cache, so the
    # password and the TOTP code never reach an error report.
    redirect_authenticated_user = False

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {"site_name": settings.SITE_NAME}


class ConsoleLogoutView(auth_views.LogoutView):
    next_page = "blog:post_list"


@require_safe
@staff_otp_required
def console(request):
    """The terminal. All of its behaviour is in static/js/writer.js."""
    return render(
        request,
        "writer/console.html",
        {
            "admin_path": settings.ADMIN_PATH,
            "boot_user": request.user.get_username(),
        },
    )
