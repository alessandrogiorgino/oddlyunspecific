import base64
import importlib.util
import io
import json
import os
import struct
import time
import zlib
from pathlib import Path
from unittest import mock

from axes.models import AccessAttempt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.utils import OperationalError
from django.test import TestCase, override_settings
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from PIL import Image

from apps.blog.models import Post
from apps.journal.tests import verified_login
from config.middleware import RealClientIPMiddleware

SETTINGS_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.py"


def png_bytes(size=(20, 20), colour=(200, 40, 40)):
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def png_declaring(width, height):
    """
    A valid PNG header that CLAIMS width x height, with a stub of image data.

    Hand-assembled rather than produced by `Image.new()`, because allocating a
    real 144-megapixel bitmap in order to assert that we refuse to allocate one
    would make the test suite the very thing it is testing for. Pillow reads
    the dimensions out of IHDR without decoding anything, which is exactly the
    property `store_image` relies on.
    """

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolour
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 16))
        + chunk(b"IEND", b"")
    )


def import_settings_with(**environment):
    """
    Execute config/settings.py fresh under a patched environment.

    Loaded under a throwaway module name so `sys.modules["config.settings"]`
    and the live `django.conf.settings` are both left alone — this asserts what
    the file does at import time, it does not reconfigure the running test.
    """
    spec = importlib.util.spec_from_file_location("config._settings_probe", SETTINGS_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, environment):
        spec.loader.exec_module(module)
    return module


class ApiAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create_user("me", password="x" * 20, is_staff=True)
        cls.plain = User.objects.create_user("them", password="x" * 20)

    def test_anonymous_gets_no_data(self):
        response = self.client.get(reverse("writer:api_posts"))
        self.assertEqual(response.status_code, 302)

    def test_password_only_session_gets_no_data(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("writer:api_posts"))
        self.assertEqual(response.status_code, 302)

    def test_non_staff_with_a_device_still_gets_no_data(self):
        verified_login(self.client, self.plain)
        response = self.client.get(reverse("writer:api_posts"))
        self.assertEqual(response.status_code, 302)

    def test_verified_staff_gets_data(self):
        verified_login(self.client, self.staff)
        response = self.client.get(reverse("writer:api_posts"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_console_requires_the_second_factor(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("writer:console")).status_code, 302)

    def test_console_renders_for_verified_staff(self):
        verified_login(self.client, self.staff)
        response = self.client.get(reverse("writer:console"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="csrf-token"')
        # The console is the only page allowed to run script, and only its own.
        self.assertIn("script-src 'self'", response["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", response["Content-Security-Policy"])

    def test_login_page_renders_and_asks_for_three_fields(self):
        response = self.client.get(reverse("writer:login"))
        self.assertEqual(response.status_code, 200)
        for field in ("username", "password", "otp_token"):
            self.assertContains(response, f'name="{field}"')


class LoginFlowTests(TestCase):
    """
    The single-step form. django-otp 1.7 refuses a bare token by default, so
    this is the behaviour most likely to break on a dependency bump.
    """

    PASSWORD = "a-long-enough-password-1"

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "me", password=self.PASSWORD, is_staff=True
        )
        self.device = TOTPDevice.objects.create(
            user=self.user, name="primary", confirmed=True
        )

    def current_code(self):
        totp = TOTP(
            self.device.bin_key, self.device.step, self.device.t0, self.device.digits
        )
        totp.time = time.time()
        return str(totp.token()).zfill(self.device.digits)

    def login(self, **overrides):
        return self.client.post(
            reverse("writer:login"),
            {"username": "me", "password": self.PASSWORD,
             "otp_token": self.current_code(), **overrides},
        )

    def test_password_plus_code_logs_in_and_opens_the_console(self):
        response = self.login()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse("writer:console")).status_code, 200)

    def test_password_without_a_code_is_refused(self):
        response = self.login(otp_token="")
        self.assertEqual(response.status_code, 200)  # form redisplayed
        self.assertEqual(self.client.get(reverse("writer:console")).status_code, 302)

    def test_wrong_code_is_refused(self):
        response = self.login(otp_token="000000")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(reverse("writer:console")).status_code, 302)

    def test_wrong_password_with_a_good_code_is_refused(self):
        response = self.login(password="not-the-password")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(reverse("writer:console")).status_code, 302)

    def test_recovery_code_works_once(self):
        static = StaticDevice.objects.create(user=self.user, name="backup-codes")
        StaticToken.objects.create(device=static, token="abcd1234")

        self.assertEqual(self.login(otp_token="abcd1234").status_code, 302)
        self.client.logout()
        # Single use: the token is consumed on the first success.
        self.assertEqual(self.login(otp_token="abcd1234").status_code, 200)

    def test_account_without_a_device_cannot_log_in(self):
        self.device.delete()
        response = self.login(otp_token="123456")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(reverse("writer:console")).status_code, 302)


class ApiBehaviourTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "me", password="x" * 20, is_staff=True
        )
        verified_login(self.client, self.user)

    def create(self, **payload):
        response = self.client.post(
            reverse("writer:api_posts"),
            data=json.dumps({"title": "A post", "body": "hello", **payload}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["post"]

    def test_created_posts_are_drafts(self):
        self.assertEqual(self.create()["status"], "draft")

    def test_publish_then_unpublish(self):
        post = self.create()
        self.client.post(reverse("writer:api_post_publish", args=[post["id"]]))
        self.assertTrue(Post.objects.get(pk=post["id"]).is_published)
        self.client.post(reverse("writer:api_post_unpublish", args=[post["id"]]))
        self.assertFalse(Post.objects.get(pk=post["id"]).is_published)

    def test_reserved_slug_is_refused(self):
        post = self.create()
        response = self.client.put(
            reverse("writer:api_post_detail", args=[post["id"]]),
            data=json.dumps({"slug": "write"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("reserved", response.json()["error"])

    def test_oversized_payload_is_refused(self):
        post = self.create()
        response = self.client.put(
            reverse("writer:api_post_detail", args=[post["id"]]),
            data=json.dumps({"body": "x" * (1024 * 1024 + 10)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_preview_uses_the_same_sanitiser(self):
        response = self.client.post(
            reverse("writer:api_preview"),
            data=json.dumps({"body": "<script>alert(1)</script>ok"}),
            content_type="application/json",
        )
        self.assertNotIn("<script", response.json()["html"])


class UploadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "me", password="x" * 20, is_staff=True
        )
        verified_login(self.client, self.user)

    def post_file(self, name, content, content_type):
        return self.client.post(
            reverse("writer:api_upload"),
            {"file": SimpleUploadedFile(name, content, content_type=content_type)},
        )

    def test_real_image_is_accepted(self):
        response = self.post_file("a.png", png_bytes(), "image/png")
        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.json()["url"], r"^/media/img/[0-9a-f]{2}/[0-9a-f]{64}\.png$")

    def test_script_disguised_as_an_image_is_refused(self):
        response = self.post_file("a.png", b"#!/bin/sh\nrm -rf /\n", "image/png")
        self.assertEqual(response.status_code, 415)

    def test_polyglot_loses_its_payload(self):
        # Valid PNG with a shell script glued on the end: the re-encode keeps
        # the pixels and drops everything after them.
        response = self.post_file(
            "a.png", png_bytes() + b"\n#!/bin/sh\nrm -rf /\n", "image/png"
        )
        self.assertEqual(response.status_code, 200)
        stored = settings.MEDIA_ROOT / response.json()["url"].removeprefix("/media/")
        self.assertNotIn(b"rm -rf", stored.read_bytes())
        stored.unlink()

    def test_decompression_bomb_is_refused(self):
        """
        A flat PNG compresses about 1000:1, so the byte-size cap says nothing
        about what decoding will cost. A real 12000x12000 black square is 410KB
        on the wire and used to be accepted: ~430MB resident and four seconds
        of CPU per request.
        """
        payload = png_declaring(12000, 12000)
        self.assertLess(len(payload), settings.UPLOAD_MAX_BYTES, "passes the size cap")

        response = self.post_file("bomb.png", payload, "image/png")
        self.assertEqual(response.status_code, 415)
        self.assertIn("MP", response.json()["error"])

    def test_the_bomb_is_refused_from_its_header_not_after_decoding(self):
        # The distinction that matters. Rejecting a bomb *after* paying to
        # decode it is not a defence, so this asserts load() is never reached.
        with mock.patch("PIL.Image.Image.load", side_effect=AssertionError("decoded!")):
            response = self.post_file("bomb.png", png_declaring(12000, 12000), "image/png")
        self.assertEqual(response.status_code, 415)

    def test_an_absurd_bomb_is_a_rejection_not_a_500(self):
        # Past 179 megapixels Pillow raises DecompressionBombError itself, and
        # that subclasses Exception directly — it walks straight through an
        # `except (UnidentifiedImageError, OSError, ValueError)`.
        response = self.post_file("huge.png", png_declaring(30000, 30000), "image/png")
        self.assertEqual(response.status_code, 415)

    def test_a_large_but_sane_photo_still_works(self):
        # 4000x3000 is 12MP — a phone. The megapixel ceiling exists to stop
        # bombs, and a ceiling that also stops photographs is a broken feature,
        # not a strict one. Under the longest-side limit too, so it is stored
        # at its original dimensions.
        response = self.post_file("photo.png", png_bytes(size=(4000, 3000)), "image/png")
        self.assertEqual(response.status_code, 200)
        stored = settings.MEDIA_ROOT / response.json()["url"].removeprefix("/media/")
        with Image.open(stored) as stored_image:
            self.assertEqual(stored_image.size, (4000, 3000))
        stored.unlink()

    def test_an_oversized_but_legitimate_image_is_downscaled_not_refused(self):
        # 8000 on the long side: past UPLOAD_MAX_PIXELS, well under the
        # megapixel ceiling. Resized, not rejected.
        response = self.post_file("wide.png", png_bytes(size=(8000, 2000)), "image/png")
        self.assertEqual(response.status_code, 200)
        stored = settings.MEDIA_ROOT / response.json()["url"].removeprefix("/media/")
        with Image.open(stored) as stored_image:
            self.assertEqual(max(stored_image.size), settings.UPLOAD_MAX_PIXELS)
        stored.unlink()


class RealClientIPTests(TestCase):
    """
    The header parsing that decides who gets locked out. Getting the Caddy case
    backwards (first entry instead of last) would let an attacker pin the
    lockout on any IP they like by sending their own X-Forwarded-For.
    """

    class Request:
        def __init__(self, **meta):
            self.META = {"REMOTE_ADDR": "172.18.0.9", **meta}

    def middleware(self, mode):
        instance = RealClientIPMiddleware(lambda request: None)
        instance.mode = mode
        return instance

    def test_caddy_takes_the_last_forwarded_entry(self):
        request = self.Request(HTTP_X_FORWARDED_FOR="1.1.1.1, 203.0.113.7")
        self.assertEqual(self.middleware("caddy")._client_ip(request), "203.0.113.7")

    def test_forged_forwarded_header_cannot_win(self):
        request = self.Request(HTTP_X_FORWARDED_FOR="evil, 203.0.113.7")
        self.assertEqual(self.middleware("caddy")._client_ip(request), "203.0.113.7")

    def test_garbage_is_ignored(self):
        request = self.Request(HTTP_X_FORWARDED_FOR="not-an-ip")
        self.assertIsNone(self.middleware("caddy")._client_ip(request))

    def test_direct_mode_changes_nothing(self):
        request = self.Request(HTTP_X_FORWARDED_FOR="203.0.113.7")
        self.assertIsNone(self.middleware("none")._client_ip(request))

    def test_no_other_header_is_ever_trusted(self):
        # There used to be a `cloudflare` mode that believed CF-Connecting-IP.
        # It was only sound while a firewall guaranteed the request came from
        # Cloudflare, and nothing in the code checked that. Any header a client
        # can set for itself must lose to the one Caddy appends.
        request = self.Request(
            HTTP_X_FORWARDED_FOR="203.0.113.7",
            HTTP_CF_CONNECTING_IP="198.51.100.4",
            HTTP_X_REAL_IP="198.51.100.4",
            HTTP_TRUE_CLIENT_IP="198.51.100.4",
        )
        self.assertEqual(self.middleware("caddy")._client_ip(request), "203.0.113.7")

    def test_unknown_mode_is_rejected_at_startup(self):
        # settings.py refuses to import rather than silently falling back to a
        # mode that reads no header at all.
        with self.assertRaises(ImproperlyConfigured):
            import_settings_with(TRUSTED_PROXY="cloudflare")


class SettingsGuardTests(TestCase):
    """
    Configuration mistakes that are silent, public, and easy to make once.

    These assert what `config/settings.py` does at import time — the process
    refusing to start is the whole feature.
    """

    PRODUCTION = {
        "DJANGO_DEBUG": "0",
        "DJANGO_SECRET_KEY": "k" * 60,
        "DJANGO_ALLOWED_HOSTS": "example.com",
        "JOURNAL_ENCRYPTION_KEYS": base64.urlsafe_b64encode(b"k" * 32).decode(),
    }

    def test_a_public_admin_path_is_refused(self):
        # "console-7f3a2b" is the value committed to .env.example. Copying the
        # file verbatim would mount the admin at a path anyone can read in this
        # repository — a secret only until someone opens the file it lives in.
        for path in ("console-7f3a2b", "admin", "change-me-something"):
            with self.subTest(path=path), self.assertRaises(ImproperlyConfigured):
                import_settings_with(**self.PRODUCTION, DJANGO_ADMIN_PATH=path)

    def test_a_real_admin_path_is_accepted(self):
        module = import_settings_with(
            **self.PRODUCTION, DJANGO_ADMIN_PATH="console-9f21ab03"
        )
        self.assertEqual(module.ADMIN_PATH, "console-9f21ab03")

    def test_the_guard_does_not_fire_in_development(self):
        # `make dev` mounts the admin at /admin/ and should keep working.
        module = import_settings_with(DJANGO_DEBUG="1", DJANGO_ADMIN_PATH="admin")
        self.assertEqual(module.ADMIN_PATH, "admin")


@override_settings(AXES_ENABLED=True)
class SecondFactorBruteForceTests(TestCase):
    """
    Wrong TOTP codes have to reach django-axes, and nothing gets them there on
    its own.

    `authenticate()` has already succeeded by the time the token is checked, so
    Django never fires `user_login_failed` and axes counts nothing. django-otp's
    own throttle does not cover the gap: it rejects without incrementing, so its
    delay stays at one second instead of backing off, and one guess per second
    against a six-digit code is ~8% per day.
    """

    PASSWORD = "a-long-enough-password-1"

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "me", password=self.PASSWORD, is_staff=True
        )
        self.device = TOTPDevice.objects.create(
            user=self.user, name="primary", confirmed=True
        )

    def current_code(self):
        totp = TOTP(
            self.device.bin_key, self.device.step, self.device.t0, self.device.digits
        )
        totp.time = time.time()
        return str(totp.token()).zfill(self.device.digits)

    def attempt(self, token):
        return self.client.post(
            reverse("writer:login"),
            {"username": "me", "password": self.PASSWORD, "otp_token": token},
        )

    def test_a_wrong_code_is_recorded_as_a_failed_attempt(self):
        self.attempt("000000")
        attempt = AccessAttempt.objects.get(username="me")
        self.assertEqual(attempt.failures_since_start, 1)

    def test_wrong_codes_reach_the_lockout(self):
        for _ in range(settings.AXES_FAILURE_LIMIT):
            self.attempt("000000")
        # Locked out, and the right code does not rescue it — which is the
        # whole point: the lockout has to outrank a correct credential.
        response = self.attempt(self.current_code())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.client.get(reverse("writer:console")).status_code, 302)

    def test_wrong_codes_and_wrong_passwords_share_one_budget(self):
        # Three of each is six, past the limit of five. An attacker must not be
        # able to buy a fresh allowance by switching which half they guess.
        for _ in range(3):
            self.attempt("000000")
        for _ in range(3):
            self.client.post(
                reverse("writer:login"),
                {"username": "me", "password": "wrong", "otp_token": "000000"},
            )
        self.assertEqual(self.attempt(self.current_code()).status_code, 429)

    def test_a_good_login_still_works(self):
        self.assertEqual(self.attempt(self.current_code()).status_code, 302)


class RateLimitTests(TestCase):
    """
    django-axes only counts *failed logins*. This covers everything else
    hitting the private surfaces: page loads, admin-path probes, API replays.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @override_settings(
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_LOGIN_REQUESTS=3,
        RATE_LIMIT_LOGIN_WINDOW_SECONDS=300,
    )
    def test_login_page_is_capped(self):
        url = reverse("writer:login")
        for _ in range(3):
            self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "300")

    @override_settings(
        RATE_LIMIT_ENABLED=True, RATE_LIMIT_REQUESTS=3, RATE_LIMIT_WINDOW_SECONDS=60
    )
    def test_private_surfaces_are_capped_even_without_a_session(self):
        # An anonymous request to /private/ is a redirect, not an error — and
        # a redirect is still cheap enough to be worth issuing a million times.
        for _ in range(3):
            self.client.get("/private/")
        self.assertEqual(self.client.get("/private/").status_code, 429)

    @override_settings(
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_REQUESTS=3,
        RATE_LIMIT_WINDOW_SECONDS=60,
        RATE_LIMIT_VERIFIED_REQUESTS=50,
        RATE_LIMIT_VERIFIED_WINDOW_SECONDS=60,
    )
    def test_the_console_gets_the_larger_budget(self):
        """
        The author writing a post must not be throttled by the budget meant for
        someone probing the admin path.

        Live preview is debounced at 400ms, so a writer who pauses once a
        second legitimately produces ~60 requests a minute — which is exactly
        the anonymous budget. Autosave adds three more.
        """
        user = get_user_model().objects.create_user(
            "me", password="x" * 20, is_staff=True
        )
        verified_login(self.client, user)
        url = reverse("writer:api_preview")
        for _ in range(20):  # well past the anonymous limit of 3
            response = self.client.post(
                url, data=json.dumps({"body": "typing"}), content_type="application/json"
            )
            self.assertEqual(response.status_code, 200)

    @override_settings(
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_REQUESTS=100,
        RATE_LIMIT_VERIFIED_REQUESTS=3,
        RATE_LIMIT_VERIFIED_WINDOW_SECONDS=60,
    )
    def test_the_larger_budget_is_still_a_budget(self):
        # Generous, not absent: a loop in writer.js retrying forever would pin
        # a gunicorn thread, and no human types fast enough to notice this.
        user = get_user_model().objects.create_user(
            "me", password="x" * 20, is_staff=True
        )
        verified_login(self.client, user)
        for _ in range(3):
            self.client.get(reverse("writer:api_state"))
        self.assertEqual(self.client.get(reverse("writer:api_state")).status_code, 429)

    @override_settings(
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_REQUESTS=3,
        RATE_LIMIT_VERIFIED_REQUESTS=500,
    )
    def test_a_password_only_session_does_not_get_the_larger_budget(self):
        # is_staff without a verified second factor is exactly the state a
        # stolen password produces. Same budget as an anonymous prober.
        user = get_user_model().objects.create_user(
            "me", password="x" * 20, is_staff=True
        )
        self.client.force_login(user)
        for _ in range(3):
            self.client.get(reverse("writer:api_state"))
        self.assertEqual(self.client.get(reverse("writer:api_state")).status_code, 429)

    @override_settings(
        RATE_LIMIT_ENABLED=True, RATE_LIMIT_REQUESTS=2, RATE_LIMIT_WINDOW_SECONDS=60
    )
    def test_the_public_blog_is_not_capped(self):
        # Readers, feed fetchers and crawlers are supposed to pull everything.
        for _ in range(10):
            self.assertEqual(self.client.get(reverse("blog:post_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("healthz")).status_code, 200)

    @override_settings(
        RATE_LIMIT_ENABLED=True, RATE_LIMIT_REQUESTS=1, RATE_LIMIT_WINDOW_SECONDS=60
    )
    def test_the_budget_is_per_address(self):
        # REMOTE_ADDR, because that is what RealClientIPMiddleware has already
        # normalised by the time the limiter looks — one flooding client must
        # not spend everybody else's allowance.
        self.client.get("/private/", REMOTE_ADDR="203.0.113.1")
        self.assertEqual(
            self.client.get("/private/", REMOTE_ADDR="203.0.113.1").status_code, 429
        )
        self.assertNotEqual(
            self.client.get("/private/", REMOTE_ADDR="203.0.113.2").status_code, 429
        )

    @override_settings(RATE_LIMIT_ENABLED=True, RATE_LIMIT_REQUESTS=1)
    def test_it_fails_open_when_the_cache_is_unreachable(self):
        # A throttle that takes the site down when Postgres hiccups is a worse
        # bug than the one it prevents.
        with mock.patch(
            "config.middleware.cache.add", side_effect=OperationalError("no cache")
        ):
            for _ in range(5):
                self.assertEqual(self.client.get("/private/").status_code, 302)
