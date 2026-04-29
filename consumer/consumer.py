import json
import os
import time

import django
import pika
from django.conf import settings

from sender.email_sender import send_email_message


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "email_service.settings")
django.setup()


def callback(channel, method, _properties, body):
    try:
        payload = json.loads(body.decode("utf-8"))
        send_email_message(payload)
        print(f"[x] Email sent to {payload.get('to')}")
        channel.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as exc:
        print(f"[!] Failed to process message: {exc}")
        # Requeue false to avoid infinite loops on malformed payloads
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


def start_consumer():
    credentials = pika.PlainCredentials(
        settings.RABBITMQ_USER,
        settings.RABBITMQ_PASSWORD,
    )
    parameters = pika.ConnectionParameters(
        host=settings.RABBITMQ_HOST,
        port=settings.RABBITMQ_PORT,
        credentials=credentials,
    )

    for attempt in range(1, 6):
        try:
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            channel.queue_declare(queue=settings.RABBITMQ_QUEUE, durable=True)
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
