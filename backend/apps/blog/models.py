from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .rendering import plain_text, reading_minutes, render_markdown, word_count

# Posts live at the site root (/some-title/), so their slugs must not collide
# with a real route. Checked at validation time rather than discovered as a
# 404 six months later.
RESERVED_SLUGS = {
    "admin", "write", "private", "media", "static", "healthz",
    "robots.txt", "sitemap.xml", "feed", "tag", "tags", "page", "api",
    ".well-known",
}


def validate_slug_not_reserved(value):
    if value in RESERVED_SLUGS:
        raise ValidationError(f"“{value}” is a reserved path on this site.")


class Tag(models.Model):
    name = models.CharField(max_length=40, unique=True)
    slug = models.SlugField(max_length=50, unique=True, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:50]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("blog:tag", args=[self.slug])


class PublishedPostManager(models.Manager):
    """
    The only manager the public views are allowed to touch.

    Drafts and future-dated posts are filtered out here rather than in each
    view, so forgetting the filter in one template is not a leak.
    """

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(status=Post.Status.PUBLISHED, published_at__lte=timezone.now())
        )


class Post(models.Model):
    """
    A public blog post. Public is the *only* state this table has.

    Private writing lives in apps.journal, in a different table, encrypted, and
    behind a different URL tree — so no missing `filter()` anywhere in this app
    can ever expose it.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "draft"
        PUBLISHED = "published", "published"

    title = models.CharField(max_length=200)
    slug = models.SlugField(
        max_length=200, unique=True, blank=True, validators=[validate_slug_not_reserved]
    )
    summary = models.TextField(
        blank=True, help_text="Shown in the feed. Auto-filled from the body if empty."
    )
    body = models.TextField(help_text="Markdown.")
    body_html = models.TextField(editable=False, blank=True)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="posts"
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="posts")

    words = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = models.Manager()
    published = PublishedPostManager()

    class Meta:
        ordering = ("-published_at", "-created_at")
        indexes = [models.Index(fields=("status", "-published_at"))]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("blog:post_detail", args=[self.slug])

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        # Rendering once, on write, keeps the read path to a single SELECT and
        # means the sanitiser runs even if a post is created from the shell.
        self.body_html = render_markdown(self.body)
        self.words = word_count(self.body_html)
        if not self.summary:
            self.summary = plain_text(self.body_html)[:280]
        if self.status == self.Status.PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def _unique_slug(self):
        base = slugify(self.title)[:180] or "post"
        if base in RESERVED_SLUGS:
            base = f"{base}-post"
        candidate, n = base, 2
        while Post.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base}-{n}"
            n += 1
        return candidate

    @property
    def is_published(self):
        return (
            self.status == self.Status.PUBLISHED
            and self.published_at is not None
            and self.published_at <= timezone.now()
        )

    @property
    def reading_minutes(self):
        return reading_minutes(self.words)
