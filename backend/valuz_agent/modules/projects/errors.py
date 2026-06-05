from valuz_agent.infra.errors import BadRequestError, NotFoundError


class WorkspaceNotFound(NotFoundError):
    error_code = 404_301
    message = "Workspace not found"


class ChatWorkspaceUndeletable(BadRequestError):
    error_code = 400_301
    message = "Default Chat workspace cannot be deleted"


class DuplicateRootPath(BadRequestError):
    error_code = 400_302
    message = "Another project already uses this directory"
