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


class TaskToggleTests(TestCase):
    BODY = "Set /230:\n- [ ] 2\n- [x] 7\n```\n- [ ] in code\n```\n- [ ] 8\n"

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "me", password="x" * 20, is_staff=True
        )
        cls.entry = JournalEntry.objects.create(body=cls.BODY, author=cls.user)

    def url(self):
        return reverse("journal:toggle_task", args=[self.entry.pk])

    def body(self):
        return JournalEntry.objects.get(pk=self.entry.pk).body

    def test_page_shows_a_button_per_item(self):
        verified_login(self.client, self.user)
        html = self.client.get(self.entry.get_absolute_url()).content.decode()
        self.assertEqual(html.count('class="task__box"'), 3)
        self.assertIn('value="1:"', html)  # the ticked one offers to untick

    def test_tick_and_untick(self):
        verified_login(self.client, self.user)
        response = self.client.post(self.url(), {"task": "2:x"})
        self.assertRedirects(
            response, self.entry.get_absolute_url() + "#task-2", fetch_redirect_response=False
        )
        self.assertIn("- [x] 8", self.body())
        self.assertIn("- [ ] in code", self.body())  # fenced code is not an item
        self.client.post(self.url(), {"task": "1:"})
        self.assertIn("- [ ] 7", self.body())

    def test_double_submit_is_harmless(self):
        verified_login(self.client, self.user)
        self.client.post(self.url(), {"task": "0:x"})
        self.client.post(self.url(), {"task": "0:x"})
        self.assertIn("- [x] 2", self.body())

    def test_garbage_changes_nothing(self):
        verified_login(self.client, self.user)
        for value in ("", "x", "9:x", "-1:x", "0:y"):
            self.client.post(self.url(), {"task": value})
        self.assertEqual(self.body(), self.BODY)

    def test_needs_second_factor(self):
        self.client.force_login(self.user)
        self.client.post(self.url(), {"task": "0:x"})
        self.assertEqual(self.body(), self.BODY)

    def test_other_authors_entry_is_404(self):
        other = get_user_model().objects.create_user("other", password="y" * 20, is_staff=True)
        verified_login(self.client, other)
        self.assertEqual(self.client.post(self.url(), {"task": "0:x"}).status_code, 404)
        self.assertEqual(self.body(), self.BODY)

    def test_post_without_csrf_token_is_refused(self):
        client = self.client_class(enforce_csrf_checks=True)
        verified_login(client, self.user)
        self.assertEqual(client.post(self.url(), {"task": "0:x"}).status_code, 403)
        self.assertEqual(self.body(), self.BODY)

    def test_get_is_refused(self):
        verified_login(self.client, self.user)
        self.assertEqual(self.client.get(self.url()).status_code, 405)
