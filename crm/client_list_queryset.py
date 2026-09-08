"""Shared Client list queryset builder for list, status-counts, and bulk_delete."""

from rest_framework import filters

from .client_list_filters import apply_client_list_filters


def build_client_list_queryset(
    view,
    request,
    queryset=None,
    *,
    exclude_status=False,
    apply_ordering=True,
):
    """
    Same filter pipeline as GET /clients/ before pagination.

    1. Role-scoped get_queryset (or provided queryset)
    2. DRF SearchFilter (?search=)
    3. Optional OrderingFilter
    4. apply_client_list_filters (type, status, tags, etc.)
    """
    if queryset is None:
        queryset = view.get_queryset()
    queryset = filters.SearchFilter().filter_queryset(request, queryset, view)
    if apply_ordering:
        queryset = filters.OrderingFilter().filter_queryset(request, queryset, view)
    queryset = apply_client_list_filters(
        queryset, request, exclude_status=exclude_status
    )
    return queryset
