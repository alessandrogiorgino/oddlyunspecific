from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import JournalEntry

SECRET = "the thing I would not want a stranger to read"


def verified_login(client, user):
    """Log in *and* mark the session as having passed the second factor."""
    device = TOTPDevice.objects.create(user=user, name="test", confirmed=True)
    client.force_login(user)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return device


class EncryptionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "me", password="x" * 20, is_staff=True
        )
        cls.entry = JournalEntry.objects.create(
            title="a private title", body=SECRET, author=cls.user
        )

    def test_roundtrip(self):
        self.assertEqual(JournalEntry.objects.get(pk=self.entry.pk).body, SECRET)

    def test_database_column_holds_ciphertext(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT title, body FROM journal_journalentry WHERE id = %s",
                [self.entry.pk],
            )
            title, body = cursor.fetchone()
        self.assertNotIn(SECRET, body)
        self.assertNotIn("a private title", title)
        # Fernet tokens are versioned and start with 0x80 -> "gAAAAA".
        self.assertTrue(body.startswith("gAAAAA"), body[:16])

    def test_same_plaintext_encrypts_differently_each_time(self):
        other = JournalEntry.objects.create(body=SECRET, author=self.user)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT body FROM journal_journalentry WHERE id IN (%s, %s)",
                [self.entry.pk, other.pk],
            )
            rows = [row[0] for row in cursor.fetchall()]
        self.assertNotEqual(rows[0], rows[1])

    def test_str_does_not_leak_the_title(self):
        self.assertNotIn("a private title", str(self.entry))


class AccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create_user("me", password="x" * 20, is_staff=True)
        cls.outsider = User.objects.create_user("them", password="x" * 20)
        cls.entry = JournalEntry.objects.create(
            title="mine", body=SECRET, author=cls.staff
        )

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("journal:entry_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/write/login/", response["Location"])

    def test_non_staff_cannot_enter(self):
        self.client.force_login(self.outsider)
        response = self.client.get(reverse("journal:entry_list"))
        self.assertEqual(response.status_code, 302)

    def test_staff_without_second_factor_cannot_enter(self):
        # Password-only session: exactly the state a stolen password produces.
        self.client.force_login(self.staff)
        response = self.client.get(reverse("journal:entry_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/write/login/", response["Location"])

    def test_staff_with_second_factor_can_read(self):
        verified_login(self.client, self.staff)
        response = self.client.get(self.entry.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, SECRET)

    def test_entries_are_scoped_to_their_author(self):
        self.outsider.is_staff = True
        self.outsider.save()
        verified_login(self.client, self.outsider)
        response = self.client.get(self.entry.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_private_pages_are_noindex_and_uncached(self):
        verified_login(self.client, self.staff)
        response = self.client.get(reverse("journal:entry_list"))
        self.assertIn("noindex", response["X-Robots-Tag"])
        self.assertIn("no-store", response["Cache-Control"])
