from valuz_agent.infra.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnprocessableEntityError,
    ValuzError,
)


class SessionNotFound(NotFoundError):
    error_code = 404_401
    message = "Session not found"


class SessionConflict(ConflictError):
    error_code = 409_401
    message = "Another session is currently running"


class SessionNotRunnable(BadRequestError):
    error_code = 400_401
    message = "Session is not in a runnable state"


class BudgetExceeded(BadRequestError):
    error_code = 400_402
    message = "Insufficient budget to proceed"


class NoChannelAvailable(BadRequestError):
    error_code = 400_403
    message = "No model channel available for this session"


class QueueFull(ConflictError):
    error_code = 409_402
    message = "Input queue is full"


class QueuedInputNotFound(NotFoundError):
    error_code = 404_402
    message = "Queued input not found"


class ForkUnsupported(UnprocessableEntityError):
    error_code = 422_401
    message = "Fork is not supported for this session"


class ForkRejected(ConflictError):
    error_code = 409_403
    message = "Fork request rejected"


class ForkRuntimeFailed(ValuzError):
    status_code = 502
    error_code = 502_401
    message = "Runtime-native fork failed"
