class DealServiceError(Exception):
    """Domain rule violation raised by DealService."""

    def __init__(self, message, *, field="detail", status_code=400):
        super().__init__(message)
        self.message = message
        self.field = field
        self.status_code = status_code
