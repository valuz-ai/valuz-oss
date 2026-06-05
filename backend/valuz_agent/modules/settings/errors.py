from valuz_agent.infra.errors import BadRequestError, NotFoundError


class SettingNotFound(NotFoundError):
    error_code = 404_801
    message = "Setting key not found"


class ShortcutConflict(BadRequestError):
    error_code = 400_801
    message = "Shortcut key combo conflicts with existing binding"


class InvalidSettingValue(BadRequestError):
    error_code = 400_802
    message = "Invalid setting value"
