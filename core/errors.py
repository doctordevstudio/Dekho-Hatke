class ApiError(Exception):
    """User-facing error. The message is safe to show in the UI / API response."""

    def __init__(self, message, status=400, code="BAD_REQUEST"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
