#!/bin/sh
set -e

echo "[entrypoint] waiting for postgres..."
until python -c "import socket,os,sys; s=socket.socket(); s.settimeout(2); \
  h=os.environ.get('DB_HOST','db'); p=int(os.environ.get('DB_PORT','5432')); \
  sys.exit(0) if s.connect_ex((h,p))==0 else sys.exit(1)" 2>/dev/null; do
  sleep 1
done
echo "[entrypoint] postgres up."

# Migrations are committed to the repo and applied here. `makemigrations` is
# deliberately NOT run at boot: generating schema changes on a production box
# hides drift and needs a writable root filesystem, which this container does
# not have.
python manage.py migrate --noinput

# Fail loudly on a misconfigured deployment instead of serving an insecure site.
if [ "$DJANGO_DEBUG" != "1" ]; then
  python manage.py check --deploy --fail-level WARNING
fi

exec "$@"
