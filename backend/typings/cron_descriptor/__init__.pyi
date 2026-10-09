from enum import IntEnum

class CasingTypeEnum(IntEnum):
    Title = 1
    Sentence = 2
    LowerCase = 3

class Options:
    locale_code: str
    casing_type: CasingTypeEnum
    verbose: bool
    day_of_week_start_index_zero: bool
    use_24hour_time_format: bool
    locale_location: str | None
    def __init__(
        self,
        casing_type: CasingTypeEnum = ...,
        *,
        verbose: bool = ...,
        day_of_week_start_index_zero: bool = ...,
        use_24hour_time_format: bool | None = ...,
        locale_code: str | None = ...,
        locale_location: str | None = ...,
    ) -> None: ...

def get_description(expression: str, options: Options | None = ...) -> str: ...
