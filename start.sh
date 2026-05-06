#!/bin/sh
set -eu

python manage.py migrate

# Inicia el consumidor de RabbitMQ en segundo plano.
python -m consumer.consumer &
CONSUMER_PID=$!

cleanup() {
  echo "Deteniendo consumer (pid=${CONSUMER_PID})..."
  kill "${CONSUMER_PID}" 2>/dev/null || true
}

trap cleanup INT TERM

# Mantiene Gunicorn en foreground para que el contenedor viva correctamente.
exec gunicorn email_service.wsgi:application --bind 0.0.0.0:${PORT:-8080}
