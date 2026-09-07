from __future__ import annotations

from kusss_to_ics.args import Args
from kusss_to_ics.models.course import Courses

# Placed here above the other imports to improve startup time.
ARGS, ACTIONS = Args.parse()

import time
from asyncio import Runner
from collections.abc import Iterable
from ctypes import ArgumentError
from itertools import chain
from pathlib import Path
from typing import Final

import structlog
import uvloop
from pydantic import RootModel
from structlog import BoundLogger
from yarl import URL

from kusss_to_ics.config import Config
from kusss_to_ics.crawler import Crawler
from kusss_to_ics.export_ics import CourseExportDescription, export_ics
from kusss_to_ics.models.program import Program, ProgramCode, ProgramDescription
from kusss_to_ics.parse_curriculum import (
    CourseDescription,
    CoursesDataFrame,
    courses_df_to_course_export_descriptions,
    parse_curriculum,
)

logger: BoundLogger = structlog.get_logger()


CACHE_DIR: Final = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True, parents=True)

CACHE_FILE: Final = CACHE_DIR / "kusss_crawl_cache.json"
KUSSS_URL_HOST: Final = "www.kusss.jku.at"


# TODO: Alternatively, support retrieving the courses from the same API that the
# my.jku.at frontend uses.
# TODO: Add a timestamp, recrawl if older than a day
# TODO: A option to directly import the appointments into Google Calendar

ProgramsList = RootModel[list[Program]]


# TODO: Consider moving the caching logic into a separate class that invokes
# the `Crawler`, would be easier to share the state that way.
async def get_cached_or_crawl(
    *,
    descriptions: Iterable[ProgramDescription],
    crawler: Crawler,
    ignore_cache: bool = False,
) -> list[Program]:
    descriptions = list(descriptions)

    log = logger.bind(cache_file=str(CACHE_FILE.resolve()))
    programs = []
    if ignore_cache:
        to_crawl = descriptions
    else:
        try:
            with CACHE_FILE.open() as handle:
                programs = ProgramsList.model_validate_json(handle.read()).root
                log.info("Was able to load the programs from the cache file.")

            cached_programs_codes = {program.code for program in programs}
            to_crawl = [desc for desc in descriptions if desc.code not in cached_programs_codes]
            if to_crawl:
                log.info("KUSSS data for some programs is not present in the cache, they have to be crawled additionally.", to_crawl_additionally=[desc.name for desc in to_crawl])
        except FileNotFoundError:
            log.info("No cache file found, forced to crawl anew.")
            to_crawl = descriptions

    if to_crawl:
        log.debug("Starting to cr")
        programs.extend(await crawler.crawl_programs(to_crawl))

        # FIXME: The timezone information is not properly serialized and
        # deserialized. The appointments restored from cache are treated as
        # UTC.
        serialized = ProgramsList(programs).model_dump_json()
        with CACHE_FILE.open("w") as handle:
            log.debug("Cached the result of the crawl into the cache file.")
            handle.write(serialized)

    return programs


async def async_main() -> None:
    config = Config.from_file(ARGS.config_file)
    log = logger.bind()

    if len(config.programs) > 1 and ARGS.primary_program_code is None:
        raise ArgumentError(
            ACTIONS.primary_program_code,
            f"More than one program is configured in the config file, but no primary program is selected, please provide a value for the `{ACTIONS.primary_program_code.option_strings[0]}` option with the program code of a configured program."
        )

    start = time.perf_counter()
    courses_dfs: dict[ProgramCode, CoursesDataFrame] = {}
    program_descriptions: dict[ProgramCode, ProgramDescription] = {}
    for config_program in config.programs:
        program_desc, courses_df = parse_curriculum(config_program.curriculum_pdf)

        courses_dfs[program_desc.code] = courses_df
        program_descriptions[program_desc.code] = program_desc

    end = time.perf_counter()

    elapsed_secs = end - start
    log.info("Parsed out the curricula.", took_secs=round(elapsed_secs, 3), program_codes=[key for key in program_descriptions])
    log.debug("Extracted programs metadata from the curricula.", programs=list(program_descriptions.values()))

    primary_program_code = ARGS.primary_program_code
    if len(config.programs) == 1:
        primary_program_code = next(iter(program_descriptions.keys()))
        if ARGS.primary_program_code is not None and ARGS.primary_program_code != primary_program_code:
            raise ArgumentError(
                ACTIONS.primary_program_code,
                "Only a single program is configured, the primary program_code provided mismatches the program code. More generally, there is no need to provide the primary program code if there is a single program configured."
            )

    if primary_program_code not in program_descriptions:
        raise ArgumentError(
            ACTIONS.primary_program_code,
            f"Invalid primary program code provided, valid program codes include: {", ".join(f"{key[0]}/{key[1]}" for key in program_descriptions)}"
        )

    primary_courses_df = courses_dfs[primary_program_code].query("sem == @ARGS.semester & is_mandatory")
    to_export_courses = courses_df_to_course_export_descriptions(primary_courses_df)

    async with Crawler() as crawler:
        extra_kusss_courses: Courses = []
        for additional_course_name_or_url in ARGS.additional_courses:
            try:
                url = URL(additional_course_name_or_url)
                if url.host != KUSSS_URL_HOST:
                    raise ValueError
                try:
                    # TODO: Cache these requests as well.
                    kusss_course = await crawler.crawl_course_by_url(
                        url=url,
                        primary_program_desc=program_descriptions[primary_program_code]
                    )
                except ValueError:
                    error_message = "Invalid course URL provided for the additional courses."
                    log.error(
                        error_message,
                        additional_course_url=url.human_repr()
                    )
                    raise RuntimeError(f"{error_message} URL: \"{url.human_repr()}\"")

                extra_kusss_courses.append(kusss_course)

            except ValueError:
                to_export_courses.append(
                    CourseExportDescription(
                        description=CourseDescription(
                            name=additional_course_name_or_url.strip()
                        ),
                        extraculicular=True,
                    )
                )

        kusss_programs = await get_cached_or_crawl(
            descriptions=program_descriptions.values(),
            crawler=crawler,
            ignore_cache=ARGS.ignore_cache,
        )
        kusss_courses = list(chain.from_iterable(program.courses for program in kusss_programs))

    export_ics(
        to_export_courses=to_export_courses,
        kusss_courses=kusss_courses,
        extra_kusss_courses=extra_kusss_courses,
        export_path=ARGS.export_path,
        split_by_course_type=ARGS.split_by_course_type
    )


if __name__ == "__main__":
    with Runner(loop_factory=uvloop.new_event_loop) as runner:
        runner.run(async_main())
