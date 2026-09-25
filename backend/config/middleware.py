"""
Two middlewares, both security-critical.

RealClientIPMiddleware normalises REMOTE_ADDR so brute-force lockouts hit the
attacker instead of the reverse proxy.

SecurityHeadersMiddleware adds the headers Django's own SecurityMiddleware does
not ship: Content-Security-Policy, Permissions-Policy, Cross-Origin-Resource-
Policy and X-Robots-Tag. The CSP is per-path, because the public pages run zero
JavaScript and should say so, while the admin needs a looser policy.
"""

import ipaddress

from django.conf import settings

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

    caddy       Caddy APPENDS the peer address to whatever X-Forwarded-For the
                client sent. A client can forge the left-hand entries; it
                cannot forge the one Caddy appends. So: take the LAST entry.
    cloudflare  Cloudflare overwrites CF-Connecting-IP on every request, and
                Caddy then appends Cloudflare's edge IP to XFF. So: trust
                CF-Connecting-IP, ignore XFF. Only safe once the origin refuses
                connections that do not come from Cloudflare's ranges.
    none        Direct connection (local dev). Leave REMOTE_ADDR alone.
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
        if self.mode == "cloudflare":
            return self._valid(request.META.get("HTTP_CF_CONNECTING_IP"))
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
