from valuz_agent.infra.errors import BadRequestError, NotFoundError


class ProviderNotFound(NotFoundError):
    error_code = 404_201
    message = "Model provider not found"


class ProviderNotDeletable(BadRequestError):
    error_code = 400_201
    message = "Managed provider cannot be deleted"


class NoAvailableProvider(BadRequestError):
    error_code = 400_202
    message = "No available model provider"


class ProviderAuthRuntimeFailure(BadRequestError):
    error_code = 400_203
    message = "Provider authentication failed at runtime"


class ProviderTestNotSupported(BadRequestError):
    error_code = 400_204
    message = "Provider does not support connection test"
