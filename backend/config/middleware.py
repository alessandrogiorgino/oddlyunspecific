"""
Three middlewares, all security-critical.

RealClientIPMiddleware normalises REMOTE_ADDR so brute-force lockouts hit the
attacker instead of the reverse proxy.

RateLimitMiddleware caps how fast one address may hit the private surfaces,
whatever it is asking for.

SecurityHeadersMiddleware adds the headers Django's own SecurityMiddleware does
not ship: Content-Security-Policy, Permissions-Policy, Cross-Origin-Resource-
Policy and X-Robots-Tag. The CSP is per-path, because the public pages run zero
JavaScript and should say so, while the admin needs a looser policy.
"""

import ipaddress
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

logger = logging.getLogger("django.security")

# Everything off. The blog needs no device API, ever.
PERMISSIONS_POLICY = ", ".join(
    f"{feature}=()"
    for feature in (
        "accelerometer",
        "autoplay",
        "camera",
        "display-capture",
        "encrypted-media",
        "fullscreen",
        "geolocation",
        "gyroscope",
        "magnetometer",
        "microphone",
        "midi",
        "payment",
        "picture-in-picture",
        "publickey-credentials-get",
        "screen-wake-lock",
        "usb",
        "xr-spatial-tracking",
    )
)

# Public pages: no script of any kind may run. If an injection ever lands in a
# post body, the browser refuses to execute it even if the sanitiser missed it.
CSP_PUBLIC = (
    "default-src 'none'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "manifest-src 'self'"
)

# Writer console: one self-hosted script file, fetch() back to this origin.
# Still no inline script, no third-party anything.
CSP_WRITER = (
    "default-src 'none'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "img-src 'self' data: blob:; "
    "style-src 'self'; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'"
)

# Django's admin still emits a handful of inline <script> and style attributes,
# so it gets the loose policy. Acceptable: reaching it already costs a password
# plus a TOTP code, and the path itself is a secret.
CSP_ADMIN = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'"
)

if settings.DEBUG:
    # Live-reload (django-browser-reload) injects a <script src> that spins up a
    # Web Worker and opens an SSE stream to /__reload__/. Governed by script-,
    # worker- and connect-src. The public policy ships none of the three; the
    # writer one already allows script and connect but not worker. Add only
    # what each is missing, and only in dev — production keeps the strict set.
    CSP_PUBLIC += "; script-src 'self'; connect-src 'self'; worker-src 'self'"
    CSP_WRITER += "; worker-src 'self'"


NOINDEX = "noindex, nofollow, noarchive, nosnippet"


class RealClientIPMiddleware:
    """
    Rewrite REMOTE_ADDR to the real client address, according to TRUSTED_PROXY.

    This has to be exactly right or django-axes locks out the proxy — which
    means locking out everyone — while the attacker keeps guessing:

    caddy   Caddy APPENDS the peer address to whatever X-Forwarded-For the
            client sent. A client can forge the left-hand entries; it cannot
            forge the one Caddy appends. So: take the LAST entry.
    none    Direct connection (local dev). Leave REMOTE_ADDR alone.

    Those are the only two modes, and settings.py rejects anything else at
    import time. There is deliberately no header here that a client could set
    for itself — a mode that trusts, say, CF-Connecting-IP is only sound while
    a firewall guarantees the request came from that provider, and a guarantee
    that lives in someone's memory of a ufw command is not one.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.mode = getattr(settings, "TRUSTED_PROXY", "none").lower()

    def __call__(self, request):
        client_ip = self._client_ip(request)
        if client_ip:
            request.META["REMOTE_ADDR"] = client_ip
        return self.get_response(request)

    def _client_ip(self, request):
        if self.mode == "caddy":
            forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
            if not forwarded:
                return None
            return self._valid(forwarded.split(",")[-1])
        return None

    @staticmethod
    def _valid(value):
        if not value:
            return None
        candidate = value.strip()
        # An IPv6 XFF entry may be bracketed, with or without a port.
        if candidate.startswith("["):
            candidate = candidate[1:].split("]")[0]
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return None


class RateLimitMiddleware:
    """
    Per-IP fixed-window cap on the private surfaces. Answers 429, cheaply.

    django-axes counts *failed logins*, which leaves plenty un-counted: GETs of
    the login page, probes looking for the admin path, replays of the JSON API
    with a valid session.

    The public blog is deliberately not rate limited. Readers, feed fetchers
    and search crawlers are supposed to be able to pull the whole site, and
    absorbing volume in front of static-ish pages is Caddy's job, not Django's.

    Three budgets, because "one request" means very different things:

    login     The two endpoints that check a password. Tightest, and it always
              applies — there is no verified session during a login.
    verified  A staff session that has passed its second factor: the author, at
              the console. Generous. The console is chatty on purpose and
              throttling the writing tool is the wrong trade. Still capped, to
              catch a runaway client rather than a person.
    private   Everyone else on /write/, /private/ and the admin path — which,
              since those surfaces let nobody else in, means a prober.

    It fails OPEN. If the cache backend is unreachable the site keeps serving:
    a throttle that can take the whole site down when Postgres hiccups is a
    worse bug than the one it prevents.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = getattr(settings, "RATE_LIMIT_ENABLED", False)
        admin_prefix = f"/{settings.ADMIN_PATH}/"
        self.private_prefixes = (admin_prefix, "/write/", "/private/")
        self.login_paths = frozenset({"/write/login/", f"{admin_prefix}login/"})
        self.limit = settings.RATE_LIMIT_REQUESTS
        self.window = settings.RATE_LIMIT_WINDOW_SECONDS
        self.login_limit = settings.RATE_LIMIT_LOGIN_REQUESTS
        self.login_window = settings.RATE_LIMIT_LOGIN_WINDOW_SECONDS
        self.verified_limit = settings.RATE_LIMIT_VERIFIED_REQUESTS
        self.verified_window = settings.RATE_LIMIT_VERIFIED_WINDOW_SECONDS

    def __call__(self, request):
        budget = self._budget_for(request)
        if budget is not None:
            name, limit, window = budget
            client_ip = request.META.get("REMOTE_ADDR") or "unknown"
            if self._over_budget(f"rl:{name}:{client_ip}", limit, window):
                logger.warning(
                    "Rate limit hit: %s asking for %s (%s budget)",
                    client_ip,
                    request.path,
                    name,
                )
                response = HttpResponse(
                    "Too many requests.\n", status=429, content_type="text/plain"
                )
                response["Retry-After"] = str(window)
                return response
        return self.get_response(request)

    def _budget_for(self, request):
        if not self.enabled:
            return None
        path = request.path
        if path in self.login_paths:
            return ("login", self.login_limit, self.login_window)
        if not path.startswith(self.private_prefixes):
            return None
        if self._is_verified_staff(request):
            return ("verified", self.verified_limit, self.verified_window)
        return ("private", self.limit, self.window)

    @staticmethod
    def _is_verified_staff(request):
        """
        The same three conditions as `staff_otp_required`, and no weaker.

        Resolving request.user costs a session query, but only when a session
        cookie was actually sent — an anonymous flood, which is the case worth
        being cheap for, never gets here.
        """
        user = getattr(request, "user", None)
        return bool(
            user is not None
            and user.is_authenticated
            and user.is_staff
            and getattr(user, "is_verified", None)
            and user.is_verified()
        )

    @staticmethod
    def _over_budget(key, limit, window):
        bucket = f"{key}:{int(time.time()) // window}"
        try:
            # add() is the atomic "first request in this window" test; incr()
            # is atomic for every one after it. The window key carries its own
            # expiry, so nothing has to be cleaned up.
            if cache.add(bucket, 1, timeout=window + 1):
                return False
            return cache.incr(bucket) > limit
        except ValueError:
            # The key expired between add() and incr() — a new window started
            # underneath us. Let it through; the next request opens the window.
            return False
        except Exception as exc:  # noqa: BLE001 - see the fail-open note above
            # One line, not a traceback: if the cache is down it is down for
            # every request, and twenty frames apiece would fill the log faster
            # than the outage fills the alerting.
            logger.warning("Rate limiter cache unavailable (%s); allowing request", exc)
            return False


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.admin_prefix = f"/{settings.ADMIN_PATH}/"

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path

        if path.startswith(self.admin_prefix):
            policy, private = CSP_ADMIN, True
        elif path.startswith("/write/"):
            policy, private = CSP_WRITER, True
        else:
            policy, private = CSP_PUBLIC, path.startswith("/private/")

        response.setdefault("Content-Security-Policy", policy)
        response.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")

        if private:
            # Belt and braces with robots.txt: a crawler that ignores the file
            # still gets told, per response, not to index or archive.
            response["X-Robots-Tag"] = NOINDEX
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response["Pragma"] = "no-cache"

        return response
