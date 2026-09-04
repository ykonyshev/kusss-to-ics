from __future__ import annotations

from datetime import date, time
from enum import Enum, auto
from typing import Annotated, Final, Literal

from pydantic import BaseModel, PlainSerializer, RootModel
from yarl import URL

type CourseType = Literal["VL", "VO", "UE", "KO", "KV", "AG", "IK", "PR", "PS", "KS", "VU", "SE"]
COURSE_TYPES: Final[set[CourseType]] = {"VL", "VO", "UE", "KO", "KV", "AG", "IK", "PR", "PS", "KS", "VU", "SE"}
COURSE_TYPES_REMAP: Final[dict[str, CourseType]] = {
    **{type_: type_ for type_ in COURSE_TYPES},
    "VO": "VL",
}


class Instructor(BaseModel):
    name: str


class Appointment(BaseModel):
    time_range: tuple[time, time]
    date: date
    room: str


class Mode(Enum):
    ON_SITE = auto()
    REMOTE = auto()
    OTHER = auto()


STRING_TO_MODE: Final = {
    "on-site": Mode.ON_SITE,
    "fernstudium": Mode.REMOTE,
}


def serialize_url(value: URL) -> str:
    return value.human_repr()


SerializableURL = Annotated[URL, PlainSerializer(serialize_url)]


class Class(BaseModel):
    code: str
    mode: Mode

    kusss_page: SerializableURL
    instructors: list[Instructor]
    appointments: list[Appointment]

    additional_info: str | None = None


class Course(BaseModel):
    name: str
    type_: CourseType
    ects: float

    classes: Classes

type Classes = list[Class]
type Instructors = list[Instructor]
type Appointments = list[Appointment]


Courses = RootModel[list[Course]]
