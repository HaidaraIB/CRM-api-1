from django.urls import path

from .views import sync_digest

urlpatterns = [
    path("digest/", sync_digest, name="sync_digest"),
]
