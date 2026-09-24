import io
import json
import time

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from PIL import Image

from apps.blog.models import Post
from apps.journal.tests import verified_login
from config.middleware import RealClientIPMiddleware


def png_bytes(size=(20, 20), colour=(200, 40, 40)):
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


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
        from django.conf import settings

        stored = settings.MEDIA_ROOT / response.json()["url"].removeprefix("/media/")
        self.assertNotIn(b"rm -rf", stored.read_bytes())
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

    def test_cloudflare_mode_ignores_forwarded_for(self):
        request = self.Request(
            HTTP_X_FORWARDED_FOR="203.0.113.7", HTTP_CF_CONNECTING_IP="198.51.100.4"
        )
        self.assertEqual(self.middleware("cloudflare")._client_ip(request), "198.51.100.4")

    def test_direct_mode_changes_nothing(self):
        request = self.Request(HTTP_X_FORWARDED_FOR="203.0.113.7")
        self.assertIsNone(self.middleware("none")._client_ip(request))
