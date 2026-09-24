from django.conf import settings


def site(request):
    """Site identity, so templates never hardcode the domain or the name."""
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_TAGLINE": settings.SITE_TAGLINE,
        "SITE_URL": settings.SITE_URL,
    }
