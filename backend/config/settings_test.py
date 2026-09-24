"""
Test settings. Used by `make test` via --settings=config.settings_test.

Only three things change, and none of them are security behaviour: the pieces
that make the suite slow or that depend on a production build artefact.
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
