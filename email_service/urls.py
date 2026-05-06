from django.urls import path
from django.http import JsonResponse


def health_check(_request):
    return JsonResponse({"status": "ok", "service": "email_service"})


urlpatterns = [
    path("health", health_check),
]
