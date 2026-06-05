from valuz_agent.infra.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnprocessableEntityError,
)


class DocumentNotFound(NotFoundError):
    error_code = 404_601
    message = "Document not found"


class ImportTaskNotFound(NotFoundError):
    error_code = 404_602
    message = "Import task not found"


class KbNotFound(NotFoundError):
    error_code = 404_603
    message = "Knowledge base not found"


class FileTooLarge(BadRequestError):
    error_code = 400_601
    message = "File exceeds 100MB limit"


class UnsupportedFileType(BadRequestError):
    error_code = 400_602
    message = "Unsupported file type"


class KbRootDuplicated(ConflictError):
    error_code = 409_601
    message = "Root path is already used by another knowledge base"


class KbRootInaccessible(UnprocessableEntityError):
    error_code = 422_601
    message = "KB root directory is not accessible"
