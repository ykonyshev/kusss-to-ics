from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Final, cast

from pydantic import BaseModel, BeforeValidator, ValidationError

PROGRAM_CODE_REGEX: Final = re.compile(r"(\d{3})(?:\s|\.|\/)?(\d{3})")


def coerce_program_code(value: str) -> tuple[str, str]:
    matched = PROGRAM_CODE_REGEX.match(value)
    if matched is None:
        raise ValidationError(f"\"{value}\" is an invalid value for a program code.")

    first_triple, second_triple = cast(tuple[str, str], matched.groups())
    return first_triple, second_triple


class Program(BaseModel):
    name: str
    code: Annotated[tuple[str, str], BeforeValidator(coerce_program_code)]
    curriculum_pdf: Path
