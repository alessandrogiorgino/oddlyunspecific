"""
Image ingest.

Uploaded bytes are never written to disk as received. Pillow decodes them and
re-encodes from the decoded pixels, which means:

  * a polyglot file (valid PNG header, shell script tail) loses the tail,
  * EXIF — including GPS coordinates from a phone photo — is dropped,
  * anything Pillow cannot decode is rejected before it lands anywhere.

The stored filename is the SHA-256 of the *re-encoded* bytes, so a URL always
means one specific image and can be cached forever.

Order matters here. `Image.open()` parses the header and stops; `image.load()`
is what commits memory to pixels. Every cheap check therefore happens between
the two, because a 400KB PNG is allowed to *claim* 12000x12000 and the cost of
believing it is ~430MB of resident memory.
"""

import hashlib
import io

from django.conf import settings
from PIL import Image, UnidentifiedImageError
from PIL.Image import DecompressionBombError

SUFFIX_BY_FORMAT = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp"}


class UploadRejected(Exception):
    pass


def store_image(uploaded_file) -> str:
    """Return the public URL of the stored image, or raise UploadRejected."""
    if uploaded_file.size > settings.UPLOAD_MAX_BYTES:
        limit = settings.UPLOAD_MAX_BYTES // (1024 * 1024)
        raise UploadRejected(f"file is larger than {limit}MB")

    raw = uploaded_file.read()

    # Header only — no pixels yet. DecompressionBombError subclasses Exception
    # directly, so it has to be named: it would walk straight past a bare
    # (UnidentifiedImageError, OSError, ValueError) and surface as a 500.
    try:
        image = Image.open(io.BytesIO(raw))
        width, height = image.size
        image_format = (image.format or "").upper()
    except (UnidentifiedImageError, DecompressionBombError, OSError, ValueError) as exc:
        raise UploadRejected("not a decodable image") from exc

    if image_format not in settings.UPLOAD_ALLOWED_FORMATS:
        allowed = ", ".join(sorted(settings.UPLOAD_ALLOWED_FORMATS))
        raise UploadRejected(f"format {image_format or 'unknown'} not allowed ({allowed})")

    # The byte-size cap says nothing about the decoded size: PNG and WEBP both
    # compress a flat image by ~1000:1, so 8MB of upload can mean gigabytes of
    # bitmap. Pillow's own guard does not raise below 179 megapixels (and only
    # warns below 89), which is far past what this container is sized for.
    total_pixels = width * height
    if total_pixels > settings.UPLOAD_MAX_TOTAL_PIXELS:
        raise UploadRejected(
            f"image is {width}x{height} ({total_pixels // 1_000_000}MP); the limit "
            f"is {settings.UPLOAD_MAX_TOTAL_PIXELS // 1_000_000}MP"
        )

    # Only now is it worth decoding.
    try:
        image.load()
    except (DecompressionBombError, OSError, ValueError) as exc:
        raise UploadRejected("not a decodable image") from exc

    limit = settings.UPLOAD_MAX_PIXELS
    if max(image.size) > limit:
        image.thumbnail((limit, limit), Image.LANCZOS)

    buffer = io.BytesIO()
    if image_format == "JPEG":
        # No exif= argument: the metadata is simply not carried over.
        image.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True)
    elif image_format == "PNG":
        # No pnginfo= argument: text chunks are dropped the same way.
        image.save(buffer, format="PNG", optimize=True)
    elif image_format == "WEBP":
        image.save(buffer, format="WEBP", quality=85, method=4)
    else:  # GIF — re-encoded flat, so an animation keeps its first frame only.
        image.convert("P", palette=Image.ADAPTIVE).save(buffer, format="GIF")

    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    suffix = SUFFIX_BY_FORMAT[image_format]

    directory = settings.MEDIA_ROOT / "img" / digest[:2]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}{suffix}"
    if not path.exists():
        # Write then rename, so a half-written file is never servable.
        temp = path.with_suffix(suffix + ".part")
        temp.write_bytes(payload)
        temp.replace(path)

    return f"/media/img/{digest[:2]}/{digest}{suffix}"
