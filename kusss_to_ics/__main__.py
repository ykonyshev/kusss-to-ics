from typing import Final

from kusss_to_ics.args import Args

# Placed here above the other imports to improve startup time.
ARGS: Final = Args.parse()

import datetime
from asyncio import Runner
from pathlib import Path

import structlog
import uvloop
from ics import Calendar, Event
from structlog import BoundLogger

from kusss_to_ics.config import Config
from kusss_to_ics.crawler import Crawler
from kusss_to_ics.models.course import Courses
from kusss_to_ics.parse_curriculum import (
    curriculum_df_to_course_descriptions,
    parse_curriculum,
)

logger: BoundLogger = structlog.get_logger()


CACHE_DIR: Final = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True, parents=True)

CACHE_FILE: Final = CACHE_DIR / "kusss_crawl_cache.json"

async def async_main() -> None:
    config = Config.from_file(ARGS.config_file)

    for program in config.programs:
        log = logger.bind(program=program.name)

        curriculum = parse_curriculum(program.curriculum_pdf)  
        mandatory_sem_courses_df = curriculum.query(f"is_mandatory & sem == {ARGS.semester}")
        mandatory_sem_courses = curriculum_df_to_course_descriptions(mandatory_sem_courses_df)

        try:
            with CACHE_FILE.open() as handle:
                program_courses = Courses.model_validate_json(handle.read())
        except FileNotFoundError:
            async with Crawler() as crawler:
                program_courses = await crawler.retrieve_courses(program)
                serialized = program_courses.model_dump_json()
                with CACHE_FILE.open("w") as handle:
                    handle.write(serialized)

        name_and_type_to_kusss_course = {
            (course.name, course.type_): course
            for course in program_courses.root
        }

        calendar = Calendar()
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

                    calendar.events.add(appointment_event)

        export_path = ARGS.export_path
        with open(export_path, "w") as handle:
            handle.write(calendar.serialize())
            log.info("Exported the ICS file.", export_path=export_path)


if __name__ == "__main__":
    with Runner(loop_factory=uvloop.new_event_loop) as runner:
        runner.run(async_main())
