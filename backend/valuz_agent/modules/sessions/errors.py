from valuz_agent.infra.errors import BadRequestError, ConflictError, NotFoundError


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
