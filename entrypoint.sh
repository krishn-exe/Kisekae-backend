set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec gunicorn KiseKaeDjango.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers 4 \
    --threads 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    --log-level info
