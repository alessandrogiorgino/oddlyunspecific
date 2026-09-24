from collections import OrderedDict

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template import loader
from django.views.decorators.http import require_safe

from .models import Post, Tag


def _can_see_drafts(request):
    """Staff *with a verified second factor* — is_staff alone is not enough."""
    user = request.user
    return (
        user.is_authenticated
        and user.is_staff
        and hasattr(user, "is_verified")
        and user.is_verified()
    )


def _by_year(posts):
    """Group an already-ordered queryset into {year: [posts]} for the index."""
    grouped = OrderedDict()
    for post in posts:
        grouped.setdefault(post.published_at.year, []).append(post)
    return grouped


@require_safe
def post_list(request):
    posts = Post.published.prefetch_related("tags")
    return render(
        request,
        "blog/post_list.html",
        {"years": _by_year(posts), "count": len(posts)},
    )


@require_safe
def post_detail(request, slug):
    # Public readers only ever query the published manager. Drafts are visible
    # at their real URL to a fully authenticated author, which is what makes
    # "preview" in the writer console work without a second code path.
    queryset = Post.objects if _can_see_drafts(request) else Post.published
    post = get_object_or_404(queryset.prefetch_related("tags"), slug=slug)
    return render(
        request,
        "blog/post_detail.html",
        {"post": post, "is_preview": not post.is_published},
    )


@require_safe
def tag(request, slug):
    tag_obj = get_object_or_404(Tag, slug=slug)
    posts = Post.published.filter(tags=tag_obj).prefetch_related("tags")
    return render(
        request,
        "blog/tag.html",
        {"tag": tag_obj, "years": _by_year(posts), "count": len(posts)},
    )


# --- Error handlers ----------------------------------------------------------


def not_found(request, exception):  # noqa: ARG001
    return render(request, "404.html", status=404)


def server_error(request):  # noqa: ARG001
    # Rendered with no context processors and no `user` access: a 500 is often
    # the database being unreachable, and an error page that itself queries the
    # database turns a readable error into an opaque one.
    return HttpResponse(loader.get_template("500.html").render({}), status=500)
