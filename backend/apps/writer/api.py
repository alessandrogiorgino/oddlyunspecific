"""
JSON endpoints for the terminal console.

Plain Django views rather than DRF: the whole surface is eight endpoints used
by one authenticated person, and every dependency added here is a dependency
that has to be patched later.

Every endpoint is wrapped in @staff_otp_required, so session + staff + a TOTP
code verified this session. Unsafe methods additionally go through Django's
CSRF middleware — the console sends the token in an X-CSRFToken header, and the
token itself lives in the session (CSRF_USE_SESSIONS), not in a cookie.
"""

import json

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.decorators import staff_otp_required
from apps.blog.models import Post, Tag
from apps.blog.rendering import render_markdown
from apps.journal.models import JournalEntry

from .uploads import UploadRejected, store_image

MAX_BODY_BYTES = 1024 * 1024  # 1MB of markdown is a very long post


def _payload(request):
    if len(request.body) > MAX_BODY_BYTES:
        raise ValueError("payload too large")
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError as exc:
        raise ValueError("malformed JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def _fail(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def _post_summary(post):
    return {
        "id": post.pk,
        "title": post.title,
        "slug": post.slug,
        "status": post.status,
        "published_at": post.published_at.isoformat() if post.published_at else None,
        "words": post.words,
        "tags": [tag.name for tag in post.tags.all()],
        "url": post.get_absolute_url(),
    }


def _post_detail(post):
    return _post_summary(post) | {"summary": post.summary, "body": post.body}


def _entry_summary(entry):
    return {
        "id": entry.pk,
        "title": entry.display_title,
        "entry_date": entry.entry_date.isoformat(),
        "pinned": entry.pinned,
        "url": entry.get_absolute_url(),
    }


def _entry_detail(entry):
    return _entry_summary(entry) | {"body": entry.body}


def _apply_tags(post, names):
    tags = []
    for raw in names:
        name = str(raw).strip()[:40]
        if not name:
            continue
        # Case-insensitive match, then create — get_or_create cannot be used
        # with an `__iexact` lookup because it would pass it straight to
        # create() as a field name.
        tag = Tag.objects.filter(name__iexact=name).first()
        if tag is None:
            tag = Tag.objects.create(name=name)
        tags.append(tag)
    post.tags.set(tags)


# --- Posts -------------------------------------------------------------------


@staff_otp_required
@require_http_methods(["GET", "POST"])
def posts(request):
    if request.method == "GET":
        queryset = Post.objects.prefetch_related("tags")
        status = request.GET.get("status")
        if status in dict(Post.Status.choices):
            queryset = queryset.filter(status=status)
        return JsonResponse({"ok": True, "posts": [_post_summary(p) for p in queryset]})

    try:
        data = _payload(request)
    except ValueError as exc:
        return _fail(str(exc))

    post = Post(
        title=(data.get("title") or "untitled").strip()[:200],
        body=data.get("body") or "",
        summary=(data.get("summary") or "").strip(),
        author=request.user,
    )
    post.save()
    _apply_tags(post, data.get("tags") or [])
    return JsonResponse({"ok": True, "post": _post_detail(post)}, status=201)


@staff_otp_required
@require_http_methods(["GET", "PUT", "DELETE"])
def post_detail(request, pk):
    try:
        post = Post.objects.prefetch_related("tags").get(pk=pk)
    except Post.DoesNotExist:
        return _fail("no such post", status=404)

    if request.method == "GET":
        return JsonResponse({"ok": True, "post": _post_detail(post)})

    if request.method == "DELETE":
        post.delete()
        return JsonResponse({"ok": True, "deleted": pk})

    try:
        data = _payload(request)
    except ValueError as exc:
        return _fail(str(exc))

    if "title" in data:
        post.title = str(data["title"]).strip()[:200] or post.title
    if "slug" in data and str(data["slug"]).strip():
        post.slug = str(data["slug"]).strip()[:200]
    if "summary" in data:
        post.summary = str(data["summary"]).strip()
    if "body" in data:
        post.body = str(data["body"])

    try:
        post.full_clean(exclude=["author"])
    except Exception as exc:  # ValidationError carries the reserved-slug message
        return _fail(getattr(exc, "messages", [str(exc)])[0])

    with transaction.atomic():
        post.save()
        if "tags" in data:
            _apply_tags(post, data["tags"] or [])
    return JsonResponse({"ok": True, "post": _post_detail(post)})


@staff_otp_required
@require_POST
def post_publish(request, pk):
    try:
        post = Post.objects.get(pk=pk)
    except Post.DoesNotExist:
        return _fail("no such post", status=404)
    post.status = Post.Status.PUBLISHED
    if post.published_at is None:
        post.published_at = timezone.now()
    post.save()
    return JsonResponse({"ok": True, "post": _post_summary(post)})


@staff_otp_required
@require_POST
def post_unpublish(request, pk):
    try:
        post = Post.objects.get(pk=pk)
    except Post.DoesNotExist:
        return _fail("no such post", status=404)
    post.status = Post.Status.DRAFT
    post.save()
    return JsonResponse({"ok": True, "post": _post_summary(post)})


# --- Journal -----------------------------------------------------------------


@staff_otp_required
@require_http_methods(["GET", "POST"])
def journal(request):
    if request.method == "GET":
        entries = JournalEntry.objects.filter(author=request.user)
        return JsonResponse({"ok": True, "entries": [_entry_summary(e) for e in entries]})

    try:
        data = _payload(request)
    except ValueError as exc:
        return _fail(str(exc))

    entry = JournalEntry.objects.create(
        title=(data.get("title") or "").strip(),
        body=data.get("body") or "",
        author=request.user,
    )
    return JsonResponse({"ok": True, "entry": _entry_detail(entry)}, status=201)


@staff_otp_required
@require_http_methods(["GET", "PUT", "DELETE"])
def journal_detail(request, pk):
    try:
        entry = JournalEntry.objects.get(pk=pk, author=request.user)
    except JournalEntry.DoesNotExist:
        return _fail("no such entry", status=404)

    if request.method == "GET":
        return JsonResponse({"ok": True, "entry": _entry_detail(entry)})

    if request.method == "DELETE":
        entry.delete()
        return JsonResponse({"ok": True, "deleted": pk})

    try:
        data = _payload(request)
    except ValueError as exc:
        return _fail(str(exc))

    if "title" in data:
        entry.title = str(data["title"]).strip()
    if "body" in data:
        entry.body = str(data["body"])
    if "pinned" in data:
        entry.pinned = bool(data["pinned"])
    if data.get("entry_date"):
        parsed = parse_date(str(data["entry_date"]))
        if parsed is None:
            return _fail("entry_date must be YYYY-MM-DD")
        entry.entry_date = parsed
    entry.save()
    return JsonResponse({"ok": True, "entry": _entry_detail(entry)})


# --- Utilities ---------------------------------------------------------------


@staff_otp_required
@require_POST
def preview(request):
    try:
        data = _payload(request)
    except ValueError as exc:
        return _fail(str(exc))
    # Same renderer and same sanitiser the stored HTML goes through, so the
    # preview cannot show something the published page would strip.
    return JsonResponse({"ok": True, "html": render_markdown(data.get("body") or "")})


@staff_otp_required
@require_POST
def upload(request):
    uploaded = request.FILES.get("file")
    if uploaded is None:
        return _fail("no file in request")
    try:
        url = store_image(uploaded)
    except UploadRejected as exc:
        return _fail(str(exc), status=415)
    return JsonResponse({"ok": True, "url": url})


@staff_otp_required
@require_http_methods(["GET"])
def state(request):
    return JsonResponse(
        {
            "ok": True,
            "user": request.user.get_username(),
            "posts": Post.objects.count(),
            "drafts": Post.objects.filter(status=Post.Status.DRAFT).count(),
            "entries": JournalEntry.objects.filter(author=request.user).count(),
            "now": timezone.localtime().isoformat(timespec="seconds"),
        }
    )
