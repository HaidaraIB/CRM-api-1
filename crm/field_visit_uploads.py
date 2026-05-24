"""Validation for optional field visit location photos."""

ALLOWED_CLIENT_LOCATION_PHOTO_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}
MAX_CLIENT_LOCATION_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB


def validate_client_location_photo(upload) -> None:
    if not upload:
        return
    content_type = (getattr(upload, "content_type", None) or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CLIENT_LOCATION_PHOTO_TYPES:
        raise ValueError(
            "File type is not allowed. Use JPEG, PNG, GIF, or WebP."
        )
    size = getattr(upload, "size", None)
    if size is not None and size > MAX_CLIENT_LOCATION_PHOTO_BYTES:
        raise ValueError("Image exceeds the 5 MB limit.")
