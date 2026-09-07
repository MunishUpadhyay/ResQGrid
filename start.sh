#!/usr/bin/env bash

# Run database migrations on startup
python manage.py migrate --noinput

# Start Celery worker in the background with auto-restart loop for RSS recycling
(
  while true; do
    echo "[start.sh] Starting Celery worker process..."
    celery -A config worker --loglevel=info --pool=solo --concurrency=1
    EXIT_CODE=$?
    echo "[start.sh] Celery worker exited with code $EXIT_CODE. Restarting in 2s..."
    sleep 2
  done
) &

# Start Daphne in the foreground (Render injects $PORT environment variable)
daphne -b 0.0.0.0 -p $PORT config.asgi:application
