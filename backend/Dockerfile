# Build stage: wheels only, so the runtime image has no compiler in it.
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


# Runtime stage.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONHASHSEED=random

# libpq5 is the only native runtime dep; no build-essential, no gcc.
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --home-dir /home/app app

WORKDIR /app

COPY --from=build /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

COPY . .

# Collect static at BUILD time, not at boot: it is the only thing that would
# need a writable root filesystem, and the prod container runs read-only.
# The values below are throwaways — settings.py only needs them to import.
RUN DJANGO_DEBUG=0 \
    DJANGO_SECRET_KEY=build-time-only-never-used \
    DJANGO_ALLOWED_HOSTS=localhost \
    DJANGO_ADMIN_PATH=build \
    JOURNAL_ENCRYPTION_KEYS=YnVpbGQtdGltZS1vbmx5LWZha2Uta2V5LTMyYnl0ZXM= \
    DATABASE_URL=postgres://u:p@127.0.0.1:5432/x \
    python manage.py collectstatic --noinput --clear \
    && mkdir -p /app/media \
    && chown -R app:app /app/media /app/staticfiles

USER app

EXPOSE 8000

ENTRYPOINT ["sh", "/app/entrypoint.sh"]

# --forwarded-allow-ips: the proxy is a docker-network IP that changes on every
#   recreate, and the container is never reachable from outside that network, so
#   trusting the hop is safe here. Without it gunicorn ignores
#   X-Forwarded-Proto and Django thinks every request is plain HTTP.
# --max-requests: recycle workers to cap memory creep on a long-lived VPS.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "30", \
     "--graceful-timeout", "20", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "100", \
     "--forwarded-allow-ips", "*", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
