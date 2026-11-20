"""URL configuration for PSL AI Auction multiplayer backend."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("health", health),
    path("health/", health),
    path("admin/", admin.site.urls),
    path("api/v1/", include("app.api.urls")),
]

