import mimetypes
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.http import FileResponse, Http404, HttpResponse
from django.utils._os import safe_join
from django.views.decorators.http import require_safe

# Uploads are re-encoded to one of these by apps/writer/uploads.py, so anything
# else on disk is not ours and is not served.
SERVABLE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


@require_safe
def healthz(request):
    """
    Liveness only — deliberately does not touch Postgres.

    A DB-checking probe turns a two-second database blip into a container
    restart loop; `make prod-logs` tells you about the database far better than
    an orchestrator killing the process would.
    """
    return HttpResponse("ok\n", content_type="text/plain")


@require_safe
def robots_txt(request):
    # The admin path is intentionally absent: writing it here would publish the
    # one thing that keeps scanners off it.
    body = (
        "User-agent: *\n"
        "Disallow: /private/\n"
        "Disallow: /write/\n"
        "Allow: /\n"
        f"\nSitemap: {settings.SITE_URL}/sitemap.xml\n"
    )
    return HttpResponse(body, content_type="text/plain")


@require_safe
def serve_media(request, path):
    """
    Serve post images from the media volume.

    WhiteNoise indexes its files at startup, so it cannot serve uploads that
    appear later; this view can. It stays narrow on purpose: safe_join blocks
    traversal, the suffix allowlist blocks anything that is not an image the
    uploader produced, and nosniff stops a browser from reinterpreting the
    bytes as something executable.
    """
    try:
        full_path = safe_join(str(settings.MEDIA_ROOT), path)
    except (ValueError, SuspiciousFileOperation):
        raise Http404

    if Path(full_path).suffix.lower() not in SERVABLE_SUFFIXES:
        raise Http404
    try:
        handle = open(full_path, "rb")
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        raise Http404

    content_type, _ = mimetypes.guess_type(full_path)
    response = FileResponse(handle, content_type=content_type or "application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    # Filenames carry a content hash, so a given URL never changes meaning.
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
