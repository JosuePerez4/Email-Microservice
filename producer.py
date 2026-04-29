import json
import os

import pika


def publish_test_message():
    credentials = pika.PlainCredentials(
        os.getenv("RABBITMQ_USER", "guest"),
        os.getenv("RABBITMQ_PASSWORD", "guest"),
    )
    parameters = pika.ConnectionParameters(
        host=os.getenv("RABBITMQ_HOST", "localhost"),
        port=int(os.getenv("RABBITMQ_PORT", "5672")),
        credentials=credentials,
    )
    queue_name = os.getenv("RABBITMQ_QUEUE", "email_queue")

    payload = {
        "to": os.getenv("TEST_TO_EMAIL", "onboarding@resend.dev"),
        "subject": "Prueba microservicio con Resend",
        "message": "Hola, este correo salio desde RabbitMQ + Django + Resend.",
    }

    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=queue_name, durable=True)
    channel.basic_publish(
        exchange="",
        routing_key=queue_name,
        body=json.dumps(payload),
        properties=pika.BasicProperties(delivery_mode=2),
    )
    connection.close()
    print(f"Mensaje enviado a '{queue_name}': {payload}")


if __name__ == "__main__":
    publish_test_message()
