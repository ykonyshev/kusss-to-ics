from __future__ import annotations

import re
from typing import Final, Literal, cast

from pydantic import BaseModel

from kusss_to_ics.models.course import Courses

PROGRAM_CODE_REGEX: Final = re.compile(r"(\d{3})(?:\s|\.|\/)?(\d{3})")

def parse_program_code(string: str) -> ProgramCode | None:
    match_ = next(PROGRAM_CODE_REGEX.finditer(string), None)
    if match_ is None:
        return None
    else:
        return cast(ProgramCode, match_.groups())


type ProgramCode = tuple[str, str]
type LanguageCode = Literal["en", "de"]

class ProgramDescription(BaseModel):
    name: str
    code: ProgramCode
    language: LanguageCode


class Program(BaseModel):
    name: str
    code: ProgramCode

    courses: Courses
