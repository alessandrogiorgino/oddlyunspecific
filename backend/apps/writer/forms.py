from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import AuthenticationForm
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
        self.clean_otp(self.get_user())
        return self.cleaned_data

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
