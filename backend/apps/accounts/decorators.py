from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login


def staff_otp_required(view_func):
    """
    The gate on every private surface: writer console, journal, upload API.

    Three conditions, all required:
      * authenticated and active,
      * is_staff,
      * request.user.is_verified() — a TOTP code was accepted THIS session.

    The third is the one that matters. Without it a stolen password is enough,
    and `login_required` alone would hand an attacker the journal.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        user = request.user
        authorised = (
            user.is_authenticated
            and user.is_active
            and user.is_staff
            and user.is_verified()
        )
        if not authorised:
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        return view_func(request, *args, **kwargs)

    return _wrapped
