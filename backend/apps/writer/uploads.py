"""
Image ingest.

Uploaded bytes are never written to disk as received. Pillow decodes them and
re-encodes from the decoded pixels, which means:

  * a polyglot file (valid PNG header, shell script tail) loses the tail,
  * EXIF — including GPS coordinates from a phone photo — is dropped,
  * anything Pillow cannot decode is rejected before it lands anywhere.

The stored filename is the SHA-256 of the *re-encoded* bytes, so a URL always
means one specific image and can be cached forever.
"""

import hashlib
import io

from django.conf import settings
from PIL import Image, UnidentifiedImageError

SUFFIX_BY_FORMAT = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp"}


class UploadRejected(Exception):
    pass


def store_image(uploaded_file) -> str:
    """Return the public URL of the stored image, or raise UploadRejected."""
    if uploaded_file.size > settings.UPLOAD_MAX_BYTES:
        limit = settings.UPLOAD_MAX_BYTES // (1024 * 1024)
        raise UploadRejected(f"file is larger than {limit}MB")

    raw = uploaded_file.read()
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UploadRejected("not a decodable image") from exc

    image_format = (image.format or "").upper()
    if image_format not in settings.UPLOAD_ALLOWED_FORMATS:
        allowed = ", ".join(sorted(settings.UPLOAD_ALLOWED_FORMATS))
        raise UploadRejected(f"format {image_format or 'unknown'} not allowed ({allowed})")

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
