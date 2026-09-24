"""
Enrol a user's second factor and print the QR straight into the terminal.

    make prod-enroll USER=alessandro

Without a confirmed TOTP device the admin and the writer console are closed to
that user — the password alone opens nothing. Run this once per device.
"""

import base64
import sys
from pathlib import Path

import qrcode
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice


class Command(BaseCommand):
    help = "Create a TOTP device for a user and print the enrolment QR code."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the user's existing devices first (lost phone).",
        )
        parser.add_argument(
            "--no-backup-codes",
            action="store_true",
            help="Skip generating single-use recovery codes.",
        )
        parser.add_argument(
            "--png",
            metavar="PATH",
            help=(
                "Also write the QR as a real PNG. Terminal line spacing squashes "
                "the ASCII one enough that some scanners refuse it."
            ),
        )
        parser.add_argument(
            "--light",
            action="store_true",
            help="Draw the ASCII QR for a light terminal background.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"No user named {username!r}. Create it first.")

        if options["reset"]:
            TOTPDevice.objects.filter(user=user).delete()
            StaticDevice.objects.filter(user=user).delete()
            self.stdout.write(self.style.WARNING("Removed existing devices."))

        if TOTPDevice.objects.filter(user=user, confirmed=True).exists():
            raise CommandError(
                f"{username} already has a confirmed TOTP device. "
                "Re-run with --reset if the phone is gone."
            )

        device = TOTPDevice.objects.create(user=user, name="primary", confirmed=True)

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"TOTP enrolment for {username}"))
        self.stdout.write("")

        qr = qrcode.QRCode(border=2)
        qr.add_data(device.config_url)
        qr.print_ascii(out=sys.stdout, invert=not options["light"])

        if options["png"]:
            path = Path(options["png"])
            qr.make_image(fill_color="black", back_color="white").save(path)
            self.stdout.write("")
            self.stdout.write(f"  QR written to    : {path}")

        secret = base64.b32encode(device.bin_key).decode().rstrip("=")
        self.stdout.write("")
        self.stdout.write(f"  Manual entry key : {secret}")
        self.stdout.write(f"  otpauth URL      : {device.config_url}")
        self.stdout.write("")
        self.stdout.write(
            "  If a scanner refuses the QR above, paste either of those two lines "
            "into the app instead — 1Password takes both."
        )

        if not options["no_backup_codes"]:
            static = StaticDevice.objects.create(user=user, name="backup-codes")
            codes = [StaticToken.random_token() for _ in range(10)]
            StaticToken.objects.bulk_create(
                StaticToken(device=static, token=code) for code in codes
            )
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING("  Recovery codes (single use)"))
            for code in codes:
                self.stdout.write(f"    {code}")

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Scan the QR now — this output is the only time it is shown. "
                "Store the recovery codes somewhere that is not this server."
            )
        )
        self.stdout.write("")
