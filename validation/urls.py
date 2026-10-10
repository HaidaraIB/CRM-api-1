from django.urls import path

from validation.views import ValidationCatalogView

urlpatterns = [
    path("catalog/", ValidationCatalogView.as_view(), name="validation_catalog"),
]
