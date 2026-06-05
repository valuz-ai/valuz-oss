from valuz_agent.infra.errors import (
    BadRequestError,
    ForbiddenError,
    GoneError,
    NotFoundError,
    UnprocessableEntityError,
)


class SkillNotFound(NotFoundError):
    error_code = 404_501
    message = "Skill not found"


class SkillReadOnly(BadRequestError):
    error_code = 400_501
    message = "Cannot modify read-only skill"


class SkillImportFailed(BadRequestError):
    error_code = 400_502
    message = "Skill import failed validation"


class DuplicateSkillManifest(UnprocessableEntityError):
    error_code = 422_501
    message = "Duplicate skill manifest found (both SKILL.md and skill.md exist)"


class InvalidSkill(UnprocessableEntityError):
    error_code = 422_502
    message = "Invalid skill: manifest is missing or malformed"


class InvalidSkillRef(UnprocessableEntityError):
    error_code = 422_503
    message = "Invalid skill reference"


class OfficialSkillNotDeletable(ForbiddenError):
    error_code = 403_501
    message = "Official skills cannot be deleted"


class SourceReadonly(ForbiddenError):
    error_code = 403_502
    message = "Cannot modify a read-only skill source"


class PreviewExpired(GoneError):
    error_code = 410_501
    message = "Import preview has expired"


class ProjectConfigInvalid(UnprocessableEntityError):
    error_code = 422_504
    message = "Project configuration is invalid or corrupted"
