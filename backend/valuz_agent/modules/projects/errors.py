from valuz_agent.infra.errors import BadRequestError, NotFoundError


class ProjectNotFound(NotFoundError):
    error_code = 404_301
    message = "Project not found"


class ChatProjectUndeletable(BadRequestError):
    error_code = 400_301
    message = "Default Chat project cannot be deleted"


class DuplicateRootPath(BadRequestError):
    error_code = 400_302
    message = "Another project already uses this directory"
