from typing import Final

from kusss_to_ics.args import Args

# Placed here above the other imports to improve startup time.
ARGS: Final = Args.parse()

from collections import defaultdict
import datetime
import time
from asyncio import Runner
from pathlib import Path

import structlog
import uvloop
from ics import Calendar, Event
from structlog import BoundLogger
from structlog.contextvars import bind_contextvars

from kusss_to_ics.config import Config
from kusss_to_ics.crawler import Crawler
from kusss_to_ics.models.course import Courses, CourseType
from kusss_to_ics.models.program import Program
from kusss_to_ics.parse_curriculum import (
    CourseDescriptions,
    curriculum_df_to_course_descriptions,
    parse_curriculum,
)

logger: BoundLogger = structlog.get_logger()


CACHE_DIR: Final = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True, parents=True)

CACHE_FILE: Final = CACHE_DIR / "kusss_crawl_cache.json"
COMBINED_CALENDAR_KEY: Final = "appointments"

# TODO: Manually adding select courses by course code to be added to a separate
# calendar. Would need to implement logic to lookup the KUSSS course IDs from
# the general course code in the `Crawler`.


def export_ics(
    *,
    kusss_courses: Courses,
    mandatory_sem_courses: CourseDescriptions,
    export_path: Path,
    split_by_course_type: bool = False,
) -> None:
    log = logger.bind()
    name_and_type_to_kusss_course = {
        (course.name, course.type_): course
        for course in kusss_courses.root
    }

    calendars = defaultdict[CourseType | str, Calendar](lambda: Calendar())

    for sem_course in mandatory_sem_courses:
        key = (sem_course.name, sem_course.type_)
        kusss_course = name_and_type_to_kusss_course.get(key)
        if kusss_course is None:
            log.error("Could not find a KUSSS course that corresponds to the semester course.", course=(sem_course.type_, sem_course.name))
            raise RuntimeError("No corresponding KUSSS course found.")

        for class_ in kusss_course.classes:
            instructors_string = ", ".join(instructor.name for instructor in class_.instructors)
            event_name = f"{kusss_course.type_}: {kusss_course.name}\n{instructors_string}"

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

                if split_by_course_type:
                    calendar = calendars[kusss_course.type_]
                else:
                    calendar = calendars[COMBINED_CALENDAR_KEY]

                calendar.events.add(appointment_event)

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


async def get_cached_or_crawl(program: Program) -> Courses:
    log = logger.bind(cache_file=str(CACHE_FILE.resolve()))
    try:
        with CACHE_FILE.open() as handle:
            kusss_courses = Courses.model_validate_json(handle.read())
            log.info("Was able to load the courses from the cache file.")
    except FileNotFoundError:
        log.info("No cache file found, forced to crawl anew.")
        start = time.perf_counter()
        async with Crawler() as crawler:
            end = time.perf_counter()
            elapsed_secs = end - start

            log.info("Successfully finished crawling.", took_secs=round(elapsed_secs, 3))

            kusss_courses = await crawler.retrieve_courses(program)
            serialized = kusss_courses.model_dump_json()
            with CACHE_FILE.open("w") as handle:
                log.debug("Cached the result of the crawl into the cache file.")
                handle.write(serialized)

    return kusss_courses


async def async_main() -> None:
    config = Config.from_file(ARGS.config_file)

    if len(config.programs) > 1:
        raise NotImplementedError("Loading multiple study programs is not yet supported. Please leave a single study program definition in the configuration file.")

    for program in config.programs:
        bind_contextvars(
            program=program.name
        )

        curriculum = parse_curriculum(program.curriculum_pdf)  
        mandatory_sem_courses_df = curriculum.query(f"is_mandatory & sem == {ARGS.semester}")
        mandatory_sem_courses = curriculum_df_to_course_descriptions(mandatory_sem_courses_df)

        kusss_courses = await get_cached_or_crawl(program)

        export_ics(
            mandatory_sem_courses=mandatory_sem_courses,
            kusss_courses=kusss_courses,
            export_path=ARGS.export_path,
            split_by_course_type=ARGS.split_by_course_type
        )



if __name__ == "__main__":
    with Runner(loop_factory=uvloop.new_event_loop) as runner:
        runner.run(async_main())
