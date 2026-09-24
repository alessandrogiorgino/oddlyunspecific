from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Post, validate_slug_not_reserved
from .rendering import render_markdown


class RenderingTests(TestCase):
    def test_script_tags_are_stripped(self):
        html = render_markdown("hello <script>alert(1)</script> world")
        self.assertNotIn("<script", html)
        self.assertNotIn("alert(1)", html)

    def test_event_handlers_are_stripped(self):
        html = render_markdown('<img src="x.png" onerror="alert(1)">')
        self.assertNotIn("onerror", html)

    def test_javascript_urls_are_stripped(self):
        html = render_markdown("[click](javascript:alert(1))")
        self.assertNotIn("javascript:", html)

    def test_data_urls_are_stripped(self):
        html = render_markdown('<a href="data:text/html,<script>1</script>">x</a>')
        self.assertNotIn("data:text/html", html)

    def test_style_tags_are_stripped(self):
        html = render_markdown("<style>body{display:none}</style>text")
        self.assertNotIn("<style", html)

    def test_external_links_get_rel(self):
        html = render_markdown("[x](https://example.com)")
        self.assertIn('rel="noopener noreferrer"', html)

    def test_code_is_highlighted_server_side(self):
        html = render_markdown("```python\nx = 1\n```")
        self.assertIn("codehilite", html)

    def test_markdown_state_does_not_leak_between_calls(self):
        first = render_markdown("text[^1]\n\n[^1]: note")
        second = render_markdown("plain text")
        self.assertIn("footnote", first)
        self.assertNotIn("footnote", second)


class SlugTests(TestCase):
    def test_reserved_slug_rejected(self):
        with self.assertRaises(ValidationError):
            validate_slug_not_reserved("write")

    def test_auto_slug_avoids_reserved_words(self):
        user = get_user_model().objects.create_user("a", password="x" * 20)
        post = Post.objects.create(title="private", body="x", author=user)
        self.assertNotEqual(post.slug, "private")


class VisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("author", password="x" * 20)
        cls.published = Post.objects.create(
            title="Out in the open",
            body="visible body",
            status=Post.Status.PUBLISHED,
            author=cls.user,
        )
        cls.draft = Post.objects.create(
            title="Still cooking", body="secret draft", author=cls.user
        )
        cls.future = Post.objects.create(
            title="Later",
            body="not yet",
            status=Post.Status.PUBLISHED,
            published_at=timezone.now() + timezone.timedelta(days=3),
            author=cls.user,
        )

    def test_index_shows_only_published(self):
        response = self.client.get(reverse("blog:post_list"))
        self.assertContains(response, "Out in the open")
        self.assertNotContains(response, "Still cooking")
        self.assertNotContains(response, "Later")

    def test_index_shows_an_excerpt(self):
        response = self.client.get(reverse("blog:post_list"))
        self.assertContains(response, "visible body")
        # The excerpt is plain text, never the raw markdown or the stored HTML.
        self.assertNotContains(response, "<p>visible body</p>")

    def test_long_bodies_are_truncated_on_the_index(self):
        Post.objects.create(
            title="Long one",
            body="word " * 300,
            status=Post.Status.PUBLISHED,
            author=self.user,
        )
        html = self.client.get(reverse("blog:post_list")).content.decode()
        excerpts = [
            chunk.split("</p>")[0] for chunk in html.split('class="excerpt">')[1:]
        ]
        long_excerpt = next(e for e in excerpts if e.startswith("word"))
        self.assertLessEqual(len(long_excerpt), 181)
        self.assertTrue(long_excerpt.endswith("…"))

    def test_hand_written_summary_wins(self):
        Post.objects.create(
            title="With a summary",
            body="the body text",
            summary="a deliberate one-liner",
            status=Post.Status.PUBLISHED,
            author=self.user,
        )
        response = self.client.get(reverse("blog:post_list"))
        self.assertContains(response, "a deliberate one-liner")

    def test_draft_url_is_404_for_the_public(self):
        response = self.client.get(self.draft.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_future_post_is_404_until_its_date(self):
        response = self.client.get(self.future.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_feed_excludes_drafts(self):
        response = self.client.get(reverse("blog:feed"))
        self.assertNotContains(response, "Still cooking")

    def test_sitemap_excludes_drafts(self):
        response = self.client.get("/sitemap.xml")
        self.assertNotContains(response, self.draft.slug)


class TemplateCommentTests(TestCase):
    """
    Django's {# #} comment is single-line only. Spanning one over two lines
    renders it into the page as ordinary text, which is how a note about
    post.summary once ended up displayed above the archive.
    """

    def assert_no_leaked_comment(self, response):
        html = response.content.decode()
        for marker in ("{#", "#}", "{% comment", "endcomment"):
            self.assertNotIn(marker, html)

    def test_index_renders_no_comment(self):
        self.assert_no_leaked_comment(self.client.get(reverse("blog:post_list")))

    def test_post_page_renders_no_comment(self):
        user = get_user_model().objects.create_user("a", password="x" * 20)
        post = Post.objects.create(
            title="T", body="b", status=Post.Status.PUBLISHED, author=user
        )
        self.assert_no_leaked_comment(self.client.get(post.get_absolute_url()))

    def test_login_page_renders_no_comment(self):
        self.assert_no_leaked_comment(self.client.get(reverse("writer:login")))


class HeaderTests(TestCase):
    def test_public_pages_forbid_all_script(self):
        response = self.client.get(reverse("blog:post_list"))
        csp = response["Content-Security-Policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn("script-src", csp)

    def test_permissions_policy_is_present(self):
        response = self.client.get(reverse("blog:post_list"))
        self.assertIn("geolocation=()", response["Permissions-Policy"])

    def test_robots_does_not_advertise_the_admin_path(self):
        response = self.client.get("/robots.txt")
        body = response.content.decode()
        self.assertIn("Disallow: /private/", body)
        self.assertNotIn("console-", body)
