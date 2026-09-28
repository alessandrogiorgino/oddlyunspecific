from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.signals import user_login_failed
from django_otp.forms import OTPAuthenticationFormMixin
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice


class SingleStepOTPMixin(OTPAuthenticationFormMixin, forms.Form):
    """
    Username, password and second factor on one form, one POST.

    django-otp's own forms stopped accepting a bare token in 1.7: they refuse
    with "please select a device" unless the request names one. The reason is
    sound — the old behaviour tried the token against every device in turn and
    recorded a failed attempt on each one that did not match, which could
    throttle a recovery-code device nobody had touched.

    This keeps one step and still touches exactly one device, by picking it
    from the shape of what was typed:

        six digits      -> the confirmed TOTP device (the authenticator app)
        anything else   -> the static device (a single-use recovery code)

    So a wrong TOTP code counts against the TOTP device only, and a wrong
    recovery code against the static device only. Nothing is ever tried twice.
    """

    otp_error_messages = dict(
        OTPAuthenticationFormMixin.otp_error_messages,
        no_device=(
            "This account has no second factor enrolled, so it cannot log in. "
            "Run `make prod-enroll USER=<name>` on the server."
        ),
    )

    # Declared on a forms.Form subclass, not on a bare mixin: Django's form
    # metaclass only collects declared_fields from Form bases, so fields on a
    # plain mixin are silently dropped — which looks exactly like "the token
    # was never submitted".
    otp_device = forms.CharField(required=False, widget=forms.HiddenInput)
    otp_token = forms.CharField(
        required=False,
        label="second factor",
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code"}),
    )
    otp_challenge = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean(self):
        super().clean()
        user = self.get_user()
        try:
            self.clean_otp(user)
        except forms.ValidationError:
            if user is not None:
                self._report_second_factor_failure(user)
            raise
        return self.cleaned_data

    def _report_second_factor_failure(self, user):
        """
        Tell django-axes that this attempt failed. Nothing else will.

        By the time the token is checked, `authenticate()` has already returned
        a user — the password was right — so Django never fires
        `user_login_failed` and axes never counts the attempt. Left alone, a
        stolen password reduces the account to a 6-digit code with no lockout
        behind it: django-otp's own throttle rejects without incrementing its
        counter, so the delay stays at one second forever instead of backing
        off, and one guess per second against 10^6 codes is a real attack.

        Firing the signal here puts wrong codes on the same AccessAttempt
        ledger as wrong passwords, so AXES_FAILURE_LIMIT covers both.
        """
        # The key is the literal string "username" because that is what
        # `AuthenticationForm` passes to `authenticate()`, and therefore what
        # axes reads out of `credentials` for a wrong *password*. Same key or
        # the two kinds of failure end up on two different ledgers, which is
        # the bug this method exists to fix.
        user_login_failed.send(
            sender=self.__class__,
            credentials={"username": self.cleaned_data.get("username") or user.get_username()},
            request=getattr(self, "request", None),
        )

    def _chosen_device(self, user):
        # An explicit otp_device wins if one was posted; the mixin already
        # checks that it belongs to this user.
        chosen = super()._chosen_device(user)
        if chosen is not None:
            return chosen

        token = (self.cleaned_data.get("otp_token") or "").strip()
        if len(token) == 6 and token.isdigit():
            device = TOTPDevice.objects.devices_for_user(user, confirmed=True).first()
        else:
            device = StaticDevice.objects.devices_for_user(user, confirmed=True).first()

        if device is None:
            raise forms.ValidationError(
                self.otp_error_messages["no_device"], code="no_device"
            )
        return device


class ConsoleAuthenticationForm(SingleStepOTPMixin, AuthenticationForm):
    """Login for /write/."""


class ConsoleAdminAuthenticationForm(SingleStepOTPMixin, AdminAuthenticationForm):
    """Login for the Django admin, so it behaves the same way."""
