from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.blog.rendering import plain_text, render_markdown

from .fields import EncryptedTextField


class JournalEntry(models.Model):
    """
    Private writing. A separate table from Post, on purpose.

    There is no `is_public` flag anywhere in this project. Public and private
    writing are different models, in different apps, under different URLs, with
    different access decorators — so the usual way blogs leak private posts
    (one forgotten `.filter(public=True)`) has nowhere to happen here.

    title and body are encrypted at rest; created_at and entry_date are not,
    because ordering a list needs them. So a database thief learns *when* you
    wrote and nothing about *what*.
    """

    title = EncryptedTextField(blank=True)
    body = EncryptedTextField(blank=True)

    # Plaintext metadata — the only thing a stolen dump reveals.
    entry_date = models.DateField(default=timezone.localdate, db_index=True)
    pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="journal_entries"
    )

    class Meta:
        ordering = ("-pinned", "-entry_date", "-created_at")
        verbose_name_plural = "journal entries"

    def __str__(self):
        # Never return the decrypted title: __str__ ends up in admin logs and
        # in exception messages, both of which get written to disk.
        return f"entry #{self.pk} · {self.entry_date}"

    def get_absolute_url(self):
        return reverse("journal:entry_detail", args=[self.pk])

    @property
    def display_title(self):
        return self.title.strip() or f"untitled · {self.entry_date}"

    @property
    def html(self):
        """
        Rendered on read, not stored.

        Caching the HTML in the database would put the plaintext right back
        into the column the encryption exists to protect.
        """
        return render_markdown(self.body)

    @property
    def excerpt(self):
        return plain_text(self.html)[:160]
