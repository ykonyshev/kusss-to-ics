from __future__ import annotations

import re
import string
from collections.abc import Iterable

import pandas as pd
import structlog
from attrs import define
from pandas.api.typing import NAType
from structlog import BoundLogger

from kusss_to_ics.args import Args

# Placed here above the other imports to improve startup time.
ARGS, ACTIONS = Args.parse()

import datetime
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ics import Calendar, Event

from kusss_to_ics.models.course import Course, CourseType

if TYPE_CHECKING:
    from kusss_to_ics.parse_curriculum import (
        CourseDescription,
    )

logger: BoundLogger = structlog.get_logger()

COMBINED_CALENDAR_KEY: Final = "combine"
EXTRA_COURSES_CALENDAR_KEY: Final = "extra"

VOWELS: Final = set("aeiouöäü")
CONSONANTS: Final = set(string.ascii_lowercase) - VOWELS
CONSONANT_FOLLOWED_BY_A_VOWEL_REGEX: Final = re.compile(rf"[{"".join(CONSONANTS)}][{"".join(VOWELS)}]")


def generate_contractions(compound: str, min_length: int = 5) -> list[str]:
    result: list[str] = []
    for match in CONSONANT_FOLLOWED_BY_A_VOWEL_REGEX.finditer(compound, pos=min_length):
        vowel_pos = match.start() + 1
        contraction = compound[:vowel_pos] + "."
        result.append(contraction)

    return result


type CourseKey = tuple[str | NAType, str]


@define
class CourseExportDescription:
    description: CourseDescription
    extraculicular: bool = False


@define
class ExportEntry:
    course: Course
    extraculicular: bool

    def add_to_calendar(
        self,
        calendar: Calendar,
    ) -> None:
        for class_ in self.course.classes:
            instructors_string = ", ".join(instructor.name for instructor in class_.instructors)
            event_name = f"{self.course.type_}: {self.course.name}\n{instructors_string}"

            description_items: list[str] = []
            if class_.additional_info is not None:
                description_items.append(class_.additional_info)

            url_repr = class_.kusss_page.human_repr()
            description_items.extend([
                url_repr,
            ])

            for appointment in class_.appointments:
                start_time, end_time = appointment.time_range
                appointment_event = Event(
                    name=event_name,
                    begin=datetime.datetime.combine(appointment.date, start_time),
                    end=datetime.datetime.combine(appointment.date, end_time),
                    url=url_repr,
                    location=appointment.room,
                    description="\n\n".join(description_items),  # pyright: ignore[reportArgumentType]
                )

                calendar.events.add(appointment_event)


def export_ics(
    *,
    kusss_courses: Iterable[Course],
    to_export_courses: Iterable[CourseExportDescription],
    extra_kusss_courses: Iterable[Course],
    export_path: Path,
    split_by_course_type: bool = False,
) -> None:
    kusss_courses = list(kusss_courses)
    to_export_courses = list(to_export_courses)
    extra_kusss_courses = list(extra_kusss_courses)

    log = logger.bind()
    type_and_name_to_kusss_course: dict[CourseKey, list[Course]] = {
        (course.type_, course.name): [course]
        for course in kusss_courses
    }

    for course in kusss_courses:
        words = course.name.split(" ")
        assert words, "There must at least one word in the course name, unless the course name is empty."

        alt_names = generate_contractions(
            compound=course.name,
            min_length=len(words[0]) + 5
        ) + [course.name]
        for name in alt_names:
            key = (pd.NA, name)
            courses_with_name = type_and_name_to_kusss_course.get(key, None)
            if courses_with_name is None:
                courses_with_name = []
                type_and_name_to_kusss_course[key] = courses_with_name

            courses_with_name.append(course)

    to_export: list[ExportEntry] = []

    for sem_course in to_export_courses:
        key = (sem_course.description.type_, sem_course.description.name)
        matching = type_and_name_to_kusss_course.get(key)
        if matching is None:
            log.error("Could not find a KUSSS course that corresponds to the semester course.", course=key)
            log.debug("", course_names=list(type_and_name_to_kusss_course.keys()))
            raise RuntimeError("No corresponding KUSSS course found.")

        for kusss_course in matching:
            to_export.append(
                ExportEntry(
                    course=kusss_course,
                    extraculicular=sem_course.extraculicular
                )
            )

    for course in extra_kusss_courses:
        to_export.append(
            ExportEntry(
                course=course,
                extraculicular=True,
            )
        )

    calendars = defaultdict[CourseType | str, Calendar](lambda: Calendar())
    for export_entry in to_export:
        if split_by_course_type:
            if export_entry.extraculicular:
                calendar = calendars[EXTRA_COURSES_CALENDAR_KEY]
            else:
                calendar = calendars[export_entry.course.type_]
        else:
            calendar = calendars[COMBINED_CALENDAR_KEY]

        export_entry.add_to_calendar(calendar)

    if split_by_course_type:
        if not (not export_path.exists() or export_path.is_dir()):
            raise ValueError("When the `split_by_course_type` is set to `True` the `export_path` must either be a path to non-existing file system object, or a path an existing directory.")

        export_path.mkdir(exist_ok=True, parents=True)
        for course_type, calendar in calendars.items():
            export_file = (export_path / f"{course_type.lower()}_appointments").with_suffix(".ics")
            with open(export_file, "w") as handle:
                handle.write(calendar.serialize())
                log.info("Exported the ICS file.", export_path=str(export_path.resolve()), course_type=course_type)
    else:
        export_path.parent.mkdir(exist_ok=True, parents=True)
        with open(export_path, "w") as handle:
            handle.write(calendars[COMBINED_CALENDAR_KEY].serialize())
            log.info("Exported the ICS file.", export_path=str(export_path.resolve()))
