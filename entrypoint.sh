#!/bin/sh
set -e

# If starting the web server, run migrations
if [ "$1" = 'gunicorn' ]; then
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
fi

exec "$@"