"""
Test settings. Used by `make test` via --settings=config.settings_test.

Only the pieces that make the suite slow, or that depend on a production build
artefact, change here. None of it is security behaviour — the two settings that
would be (AXES_ENABLED, the rate limiter) are switched back on per-test with
`override_settings`, so the tests that care exercise the real thing.
"""

import tempfile

from .settings import *  # noqa: F403

# The real storage resolves every {% static %} through the manifest that
# `collectstatic` writes at image build time. There is no manifest in a test
# run, so use the plain backend.
STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Argon2 is deliberately slow. Multiplied by every create_user() in the suite
# it is minutes of nothing.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Uploads go to a throwaway directory, never the real media volume.
MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="oddly-test-media-"))  # noqa: F405

# The lockout is exercised in its own test; leaving it on globally makes every
# other login-heavy test order-dependent.
AXES_ENABLED = False

# Same reasoning: a per-IP cap and a test suite that makes fifty requests from
# 127.0.0.1 do not mix. RateLimitTests turns it back on.
RATE_LIMIT_ENABLED = False

# Pinned rather than inherited. Production runs the database cache so the three
# gunicorn workers share one set of counters; in tests that would just be extra
# SQL, and inheriting it would make the result depend on DJANGO_DEBUG.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
