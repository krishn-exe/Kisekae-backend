#!/bin/sh
set -e

echo "Running migrations..."
python manage.py migrate --noinput

echo "Starting Gunicorn..."
exec gunicorn KiseKaeDjango.wsgi:application --bind 0.0.0.0:8000 --workers 3