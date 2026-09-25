"""
Settings for oddlyunspecific.

Everything that differs between the laptop and the VPS comes from the
environment. The defaults are the SAFE ones: if a variable is missing in
production the process refuses to start rather than quietly serving an
insecure site. The reasoning behind each block is in README.md § Security.
"""

import base64
import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Repo-root .env, for running manage.py outside docker. In the containers the
# values arrive as real environment variables and this is a no-op.
load_dotenv(BASE_DIR.parent / ".env")


def env(name, default=None):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_bool(name, default=False):
    return env(name, "1" if default else "0") in ("1", "true", "True", "yes")


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


def require(name):
    value = env(name)
    if not value:
        raise ImproperlyConfigured(
            f"{name} is required when DJANGO_DEBUG=0. See .env.example."
        )
    return value


DEBUG = env_bool("DJANGO_DEBUG", False)

# --- Identity ----------------------------------------------------------------

SITE_NAME = env("SITE_NAME", "oddlyunspecific")
SITE_TAGLINE = env("SITE_TAGLINE", "")
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")

# --- Secrets -----------------------------------------------------------------

if DEBUG:
    SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-not-for-production")
else:
    SECRET_KEY = require("DJANGO_SECRET_KEY")
    if SECRET_KEY.startswith(("dev-", "change-me")):
        raise ImproperlyConfigured("DJANGO_SECRET_KEY still holds a placeholder.")

# Fernet keys for the private journal. First one encrypts; all of them can
# decrypt, which is what makes key rotation possible (see
# apps/journal/crypto.py and `make prod-rotate-journal`).
JOURNAL_ENCRYPTION_KEYS = env_list("JOURNAL_ENCRYPTION_KEYS")
if not JOURNAL_ENCRYPTION_KEYS and not DEBUG:
    raise ImproperlyConfigured(
        "JOURNAL_ENCRYPTION_KEYS is required. Generate one with `make secrets`."
    )
for _key in JOURNAL_ENCRYPTION_KEYS:
    try:
        if len(base64.urlsafe_b64decode(_key)) != 32:
            raise ValueError
    except Exception as exc:  # noqa: BLE001 - any decode failure is fatal
        raise ImproperlyConfigured(
            "JOURNAL_ENCRYPTION_KEYS must be url-safe base64 of exactly 32 bytes. "
            "Generate one with `make secrets`."
        ) from exc

# --- Hosts and origins -------------------------------------------------------

# localhost is always allowed so the container healthcheck and the shared
# Caddy's reachability probe can reach /healthz/ without a Host header trick.
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS") + ["localhost", "127.0.0.1"]
if DEBUG:
    ALLOWED_HOSTS = ["*"]
elif not env_list("DJANGO_ALLOWED_HOSTS"):
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS is required when DEBUG=0.")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if DEBUG:
    CSRF_TRUSTED_ORIGINS += ["http://localhost:8000", "http://127.0.0.1:8000"]

# Where the Django admin is mounted. Not /admin/ — every scanner on the
# internet probes that path within minutes of a domain going live.
ADMIN_PATH = env("DJANGO_ADMIN_PATH", "admin").strip("/")

# Which proxy, if any, is allowed to tell us the client's real IP. Getting this
# wrong means brute-force lockouts ban the proxy instead of the attacker.
TRUSTED_PROXY = env("TRUSTED_PROXY", "caddy" if not DEBUG else "none")

# --- Applications ------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    # third party
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    "axes",
    # local
    "apps.accounts",
    "apps.blog",
    "apps.journal",
    "apps.writer",
]

MIDDLEWARE = [
    # First: rewrite REMOTE_ADDR from the trusted proxy so everything below
    # (axes especially) sees the actual client.
    "config.middleware.RealClientIPMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Populates request.user.is_verified() — the second factor check.
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.middleware.SecurityHeadersMiddleware",
    # django-axes wants to be last so it sees the authenticated request.
    "axes.middleware.AxesMiddleware",
]

if DEBUG:
    # Live-reload: the browser refreshes itself on a template, CSS or Python
    # edit, so a save in the editor shows without a manual reload. Dev only —
    # the app, its middleware, its URL (config/urls.py) and the CSP widening it
    # needs (config/middleware.py) are all behind a DEBUG guard.
    INSTALLED_APPS.append("django_browser_reload")
    # After SecurityHeadersMiddleware so the CSP header is already set when the
    # reload script is injected; before AxesMiddleware, which must stay last.
    MIDDLEWARE.insert(
        MIDDLEWARE.index("config.middleware.SecurityHeadersMiddleware") + 1,
        "django_browser_reload.middleware.BrowserReloadMiddleware",
    )

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ----------------------------------------------------------------

DATABASES = {
    "default": dj_database_url.config(
        default=env("DATABASE_URL", "postgres://oddly:oddly@db:5432/oddly"),
        conn_max_age=600,
        conn_health_checks=True,
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentication ----------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

# AxesStandaloneBackend must come first: it short-circuits a login attempt from
# a locked-out IP before the password is ever checked.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Argon2id first. Existing hashes keep working and are upgraded on next login.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 14},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Shown as the account name inside the authenticator app.
OTP_TOTP_ISSUER = SITE_NAME

LOGIN_URL = "writer:login"
LOGIN_REDIRECT_URL = "writer:console"
LOGOUT_REDIRECT_URL = "blog:post_list"

# --- django-axes: brute-force lockout ----------------------------------------

# Lock on IP and on username independently, so a spray across many usernames
# from one IP and a spray at one username from many IPs are both caught.
AXES_LOCKOUT_PARAMETERS = [["ip_address"], ["username"]]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=30)
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "writer/locked_out.html"
AXES_VERBOSE = True
AXES_ENABLE_ACCESS_FAILURE_LOG = True
# REMOTE_ADDR is already the real client IP by the time axes sees it, because
# RealClientIPMiddleware rewrote it. Do not let axes re-parse proxy headers.
AXES_IPWARE_PROXY_COUNT = None
AXES_IPWARE_META_PRECEDENCE_ORDER = ("REMOTE_ADDR",)

# --- Sessions and CSRF -------------------------------------------------------

# The CSRF token lives in the session, so there is no CSRF cookie to steal or
# to leak through a subdomain. One fewer cookie, one fewer attack path.
CSRF_USE_SESSIONS = True
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Strict"

SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 60 * 60 * 12  # a writing session, not a month
if not DEBUG:
    # __Host- prefix: the browser itself refuses this cookie unless it is
    # Secure, Path=/ and has no Domain — so no subdomain can ever set it.
    SESSION_COOKIE_NAME = "__Host-session"

# --- Transport security ------------------------------------------------------

if not DEBUG:
    # Caddy terminates TLS and sets X-Forwarded-Proto; gunicorn is started with
    # --forwarded-allow-ips so it passes the header through.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    # The healthcheck and the Caddy reachability probe speak plain HTTP to the
    # container, so they must not be redirected.
    SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# --- Request limits ----------------------------------------------------------

# Caddy already caps the body at 12MB; this is the belt to that braces.
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 4 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200
DATA_UPLOAD_MAX_NUMBER_FILES = 10

# --- Static and media --------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # Hashed filenames + long cache headers, and the manifest makes a
        # missing asset a build-time error instead of a broken page.
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
# A year is right for hashed filenames in production. In development the names
# are not hashed, so the same header tells the browser to cache an unhashed
# site.css for a year and edits stop showing up until a hard refresh.
WHITENOISE_MAX_AGE = 0 if DEBUG else 31536000

if DEBUG:
    # runserver's own static handler wraps the WSGI app *outside* the middleware
    # chain, so WhiteNoise never sees /static and its max-age=0 above never
    # lands — which is why edits needed a hard refresh. Serve from the finders
    # via WhiteNoise instead (pair with runserver --nostatic); every asset then
    # carries WhiteNoise's revalidating header and a soft refresh shows changes.
    WHITENOISE_AUTOREFRESH = True
    WHITENOISE_USE_FINDERS = True

# --- Uploads -----------------------------------------------------------------

# Every upload is decoded and re-encoded by Pillow before it touches disk, so a
# polyglot file (valid JPEG *and* valid script) cannot survive the round trip.
UPLOAD_MAX_BYTES = 8 * 1024 * 1024
UPLOAD_MAX_PIXELS = 6000
UPLOAD_ALLOWED_FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}

# --- Locale ------------------------------------------------------------------

LANGUAGE_CODE = env("LANGUAGE_CODE", "en-us")
TIME_ZONE = env("TIME_ZONE", "Europe/Rome")
USE_I18N = True
USE_TZ = True

# --- Logging -----------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        # Host-header attacks, suspicious operations, CSRF failures.
        "django.security": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Every failed login and every lockout.
        "axes": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"
