import json
import os
import sqlite3
import ssl
import time
from html import escape

import django
import pika
from django.conf import settings

from sender.email_sender import send_email_message


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "email_service.settings")
django.setup()


POINTS_REQUIRED_FIELDS = ("cliente_id", "nombre", "email")
WELCOME_ROUTING_KEYS = {"envio.bienvenida.creado"}
POINTS_INCREASE_ROUTING_KEYS = {"puntos.aumentados"}
POINTS_REDEEM_ROUTING_KEYS = {"puntos.canjeados"}


def _is_transient_error(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            pika.exceptions.AMQPConnectionError,
            sqlite3.OperationalError,
        ),
    )


def _init_idempotency_store() -> None:
    conn = sqlite3.connect(settings.BASE_DIR / "db.sqlite3")
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_welcome_events (
                envio_id TEXT PRIMARY KEY,
                processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _already_processed(envio_id: str) -> bool:
    conn = sqlite3.connect(settings.BASE_DIR / "db.sqlite3")
    try:
        row = conn.execute(
            "SELECT 1 FROM processed_welcome_events WHERE envio_id = ? LIMIT 1",
            (envio_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _mark_as_processed(envio_id: str) -> None:
    conn = sqlite3.connect(settings.BASE_DIR / "db.sqlite3")
    try:
        conn.execute(
            "INSERT OR IGNORE INTO processed_welcome_events (envio_id) VALUES (?)",
            (envio_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _get_retry_count(properties) -> int:
    headers = getattr(properties, "headers", None) or {}
    x_death = headers.get("x-death")
    if isinstance(x_death, list) and x_death:
        count = x_death[0].get("count", 0)
        try:
            return int(count)
        except (TypeError, ValueError):
            return 0
    return 0


def _parse_event(body: bytes) -> dict:
    evento = json.loads(body.decode("utf-8"))
    if not isinstance(evento, dict):
        raise ValueError("Event payload must be a JSON object")
    return evento


def _get_field(evento: dict, *keys, default=None):
    for key in keys:
        value = evento.get(key)
        if value not in (None, ""):
            return value
    return default


def _normalize_event(evento: dict) -> dict:
    return {
        "event_id": _get_field(evento, "event_id", "eventId", "evento_id", "eventoId"),
        "envio_id": _get_field(evento, "envio_id", "envioId"),
        "movimiento_id": _get_field(evento, "movimiento_id", "movimientoId"),
        "cliente_id": _get_field(evento, "cliente_id", "clienteId"),
        "nombre": _get_field(evento, "nombre"),
        "email": _get_field(evento, "email"),
        "estado": _get_field(evento, "estado"),
        "direccion": _get_field(evento, "direccion"),
        "fecha": _get_field(evento, "fecha"),
        "puntos_sumados": _get_field(evento, "puntos_sumados", "puntosSumados"),
        "puntos_canjeados": _get_field(evento, "puntos_canjeados", "puntosCanjeados"),
        "puntos_actuales": _get_field(evento, "puntos_actuales", "puntosActuales"),
        "puntos": _get_field(evento, "puntos"),
    }


def _resolve_event_id(evento: dict, routing_key: str) -> str:
    if evento.get("event_id"):
        return str(evento["event_id"])
    if evento.get("envio_id"):
        return f"{routing_key}:{evento['envio_id']}"
    if evento.get("movimiento_id"):
        return f"{routing_key}:{evento['movimiento_id']}"
    if evento.get("cliente_id"):
        return f"{routing_key}:{evento['cliente_id']}:{evento.get('fecha', '')}"
    raise ValueError("Missing idempotency key. Expected event_id, envio_id or movimiento_id")


def _build_welcome_email_payload(evento: dict) -> dict:
    base_missing_fields = [field for field in ("cliente_id", "nombre", "email") if not evento.get(field)]
    if base_missing_fields:
        raise ValueError(
            f"Missing required fields for welcome event: {', '.join(base_missing_fields)}"
        )

    nombre = escape(str(evento["nombre"]))
    estado = evento.get("estado")
    envio_id = evento.get("envio_id")
    direccion = evento.get("direccion")

    html_parts = [
        f"<p>Hola {nombre},</p>",
        "<p>Bienvenido/a al sistema de puntos <strong>Loyalty Ops</strong>.</p>",
        "<p>Tu cuenta inicia con <strong>0 puntos</strong> y estamos felices de tenerte aqui.</p>",
    ]
    if estado and envio_id and direccion:
        html_parts.append(
            "<p>"
            "Tu paquete de bienvenida fue registrado con estado: "
            f"<strong>{escape(str(estado))}</strong><br/>"
            f"ID de envio: <strong>{escape(str(envio_id))}</strong><br/>"
            f"Direccion: <strong>{escape(str(direccion))}</strong>"
            "</p>"
        )
    html_parts.append(
        "<p>Te avisaremos por correo cada vez que tus puntos aumenten o cuando realices un canje.</p>"
    )
    html = "".join(html_parts)
    return {
        "to": evento["email"],
        "subject": "Bienvenido/a a Loyalty Ops",
        "html": html,
    }


def _build_points_increase_email_payload(evento: dict) -> dict:
    missing_fields = [field for field in POINTS_REQUIRED_FIELDS if not evento.get(field)]
    if missing_fields:
        raise ValueError(
            f"Missing required fields for points increase event: {', '.join(missing_fields)}"
        )

    nombre = escape(str(evento["nombre"]))
    puntos_sumados = escape(str(evento.get("puntos_sumados", evento.get("puntos", "N/A"))))
    puntos_actuales = escape(str(evento.get("puntos_actuales", "N/A")))
    html = (
        f"<p>Hola {nombre},</p>"
        "<p>Tienes una actualizacion en <strong>Loyalty Ops</strong>.</p>"
        f"<p>Se acreditaron <strong>{puntos_sumados}</strong> puntos a tu cuenta.</p>"
        f"<p>Tu saldo actual es: <strong>{puntos_actuales}</strong> puntos.</p>"
        "<p>Gracias por seguir acumulando con nosotros.</p>"
    )
    return {
        "to": evento["email"],
        "subject": "Tus puntos aumentaron en Loyalty Ops",
        "html": html,
    }


def _build_points_redeem_email_payload(evento: dict) -> dict:
    missing_fields = [field for field in POINTS_REQUIRED_FIELDS if not evento.get(field)]
    if missing_fields:
        raise ValueError(
            f"Missing required fields for points redeem event: {', '.join(missing_fields)}"
        )

    nombre = escape(str(evento["nombre"]))
    puntos_canjeados = escape(str(evento.get("puntos_canjeados", evento.get("puntos", "N/A"))))
    puntos_actuales = escape(str(evento.get("puntos_actuales", "N/A")))
    html = (
        f"<p>Hola {nombre},</p>"
        "<p>Tu canje en <strong>Loyalty Ops</strong> fue procesado correctamente.</p>"
        f"<p>Puntos canjeados: <strong>{puntos_canjeados}</strong>.</p>"
        f"<p>Tu saldo actual es: <strong>{puntos_actuales}</strong> puntos.</p>"
        "<p>Gracias por participar en el programa de puntos.</p>"
    )
    return {
        "to": evento["email"],
        "subject": "Confirmacion de canje de puntos",
        "html": html,
    }


def _build_email_payload(evento: dict, routing_key: str) -> dict:
    if routing_key in WELCOME_ROUTING_KEYS:
        return _build_welcome_email_payload(evento)
    if routing_key in POINTS_INCREASE_ROUTING_KEYS:
        return _build_points_increase_email_payload(evento)
    if routing_key in POINTS_REDEEM_ROUTING_KEYS:
        return _build_points_redeem_email_payload(evento)
    raise ValueError(f"Unsupported routing key for email notifications: {routing_key}")


def _publish_delivery_status_event(channel, evento: dict, new_status: str) -> None:
    envio_id = evento.get("envio_id")
    if not envio_id:
        return

    payload = {
        "envio_id": envio_id,
        "cliente_id": evento.get("cliente_id"),
        "nombre": evento.get("nombre"),
        "email": evento.get("email"),
        "estado": new_status,
    }
    channel.basic_publish(
        exchange=settings.RABBITMQ_EXCHANGE,
        routing_key=settings.RABBITMQ_ENVIO_ENVIADO_ROUTING_KEY,
        body=json.dumps(payload),
        properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
    )


def callback(channel, method, properties, body):
    try:
        routing_key = method.routing_key
        evento = _normalize_event(_parse_event(body))
        event_id = _resolve_event_id(evento, routing_key)

        if _already_processed(event_id):
            print(f"[i] Duplicate event ignored for id={event_id}")
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return

        email_payload = _build_email_payload(evento, routing_key)
        send_email_message(email_payload)
        if routing_key in WELCOME_ROUTING_KEYS:
            _publish_delivery_status_event(channel, evento, "ENVIADO")
        _mark_as_processed(event_id)
        print(
            f"[x] Notification email sent to {email_payload.get('to')} "
            f"(routing_key={routing_key}, id={event_id})"
        )
        channel.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as exc:
        retries = _get_retry_count(properties)
        max_retries = getattr(settings, "RABBITMQ_MAX_RETRIES", 5)
        should_retry = _is_transient_error(exc) and retries < max_retries
        print(
            f"[!] Failed to process welcome event: {exc}. "
            f"retry={should_retry} attempt={retries}/{max_retries}"
        )
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=should_retry)


def start_consumer():
    _init_idempotency_store()
    credentials = pika.PlainCredentials(
        settings.RABBITMQ_USER,
        settings.RABBITMQ_PASSWORD,
    )
    ssl_options = None
    if settings.RABBITMQ_SSL:
        context = ssl.create_default_context()
        ssl_options = pika.SSLOptions(context, settings.RABBITMQ_HOST)

    parameters = pika.ConnectionParameters(
        host=settings.RABBITMQ_HOST,
        port=settings.RABBITMQ_PORT,
        virtual_host=settings.RABBITMQ_VHOST,
        credentials=credentials,
        ssl_options=ssl_options,
    )

    for attempt in range(1, 6):
        try:
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            channel.exchange_declare(
                exchange=settings.RABBITMQ_EXCHANGE,
                exchange_type="topic",
                durable=True,
            )
            channel.queue_declare(queue=settings.RABBITMQ_QUEUE, durable=True)
            routing_keys = getattr(settings, "RABBITMQ_ROUTING_KEYS", [settings.RABBITMQ_ROUTING_KEY])
            for routing_key in routing_keys:
                channel.queue_bind(
                    queue=settings.RABBITMQ_QUEUE,
                    exchange=settings.RABBITMQ_EXCHANGE,
                    routing_key=routing_key,
                )
                print(
                    f"[*] Queue '{settings.RABBITMQ_QUEUE}' bound to "
                    f"'{settings.RABBITMQ_EXCHANGE}' with key '{routing_key}'"
                )
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(
                queue=settings.RABBITMQ_QUEUE,
                on_message_callback=callback,
            )
            print(f"[*] Waiting for messages in '{settings.RABBITMQ_QUEUE}'")
            channel.start_consuming()
            break
        except pika.exceptions.AMQPConnectionError:
            print(f"[!] RabbitMQ unavailable. Retry {attempt}/5...")
            time.sleep(3)


if __name__ == "__main__":
    start_consumer()
