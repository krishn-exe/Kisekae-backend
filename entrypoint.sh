#!/bin/sh
set -e

# If starting the web server, wait for the database, then run migrations
if [ "$1" = 'gunicorn' ]; then
    echo "Waiting for database..."
    until python -c "import psycopg2, os; psycopg2.connect(dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'), host=os.getenv('DB_HOST', 'db'), port=os.getenv('DB_PORT', '5432'), connect_timeout=2)" 2>/dev/null; do
        sleep 1
    done

    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
fi

exec "$@"
