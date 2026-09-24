from django.conf import settings
from django.contrib.syndication.views import Feed
from django.urls import reverse

from .models import Post


class PostFeed(Feed):
    """
    RSS over published posts only.

    It reads Post.published like every other public surface, so the journal can
    never appear here — different table, different app.
    """

    title = settings.SITE_NAME
    description = settings.SITE_TAGLINE or settings.SITE_NAME
    link = "/"

    def items(self):
        return Post.published.all()[:30]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        # Already sanitised at save time.
        return item.body_html

    def item_link(self, item):
        return reverse("blog:post_detail", args=[item.slug])

    def item_pubdate(self, item):
        return item.published_at

    def item_updateddate(self, item):
        return item.updated_at
