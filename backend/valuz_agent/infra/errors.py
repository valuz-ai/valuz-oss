class ValuzError(Exception):
    status_code: int = 500
    error_code: int = 500000
    message: str = "Internal server error"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.__class__.message
        super().__init__(self.message)


class BadRequestError(ValuzError):
    status_code = 400
    error_code = 400000


class NotFoundError(ValuzError):
    status_code = 404
    error_code = 404000


class ConflictError(ValuzError):
    status_code = 409
    error_code = 409000


class UnprocessableEntityError(ValuzError):
    status_code = 422
    error_code = 422000


class ForbiddenError(ValuzError):
    status_code = 403
    error_code = 403000


class GoneError(ValuzError):
    status_code = 410
    error_code = 410000


class RuntimeBootstrapError(RuntimeError):
    """Raised when the runtime container cannot be assembled."""
