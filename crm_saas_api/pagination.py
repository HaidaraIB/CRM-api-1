"""DRF pagination: allow clients to set ``page_size`` with a server-enforced cap."""

from django.conf import settings
from rest_framework.pagination import PageNumberPagination


class FlexiblePageNumberPagination(PageNumberPagination):
    """
    Default list pagination with optional ``?page_size=`` (capped by DRF_MAX_PAGE_SIZE).
    """

    page_size_query_param = "page_size"

    def __init__(self):
        super().__init__()
        self.max_page_size = int(getattr(settings, "DRF_MAX_PAGE_SIZE", 200))
