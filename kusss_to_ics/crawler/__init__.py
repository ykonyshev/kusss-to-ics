from __future__ import annotations

import time
from asyncio import TaskGroup
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Self

from structlog import BoundLogger, get_logger
from yarl import URL

from kusss_to_ics.crawler.program_crawler import ALL_COURSE_CLASSES_PAGE, BASE_KUSSS_URL
from kusss_to_ics.crawler.program_crawler import ProgramCrawler as ProgramCrawler
from kusss_to_ics.models.course import (
    Course,
)
from kusss_to_ics.models.program import Program, ProgramCode, ProgramDescription

logger: BoundLogger = get_logger()


class Crawler(AbstractAsyncContextManager):
    def __init__(self) -> None:
        self._log = logger.bind()

        self._program_crawlers: dict[ProgramCode, ProgramCrawler] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /
    ) -> None:
        async with TaskGroup() as tg:
            for program_crawler in self._program_crawlers.values():
                tg.create_task(program_crawler.__aexit__(exc_type, exc_value, traceback))

    async def _get_program_crawler(self, desc: ProgramDescription) -> ProgramCrawler:
        maybe_crawler = self._program_crawlers.get(desc.code)
        if maybe_crawler is None:
            crawler = ProgramCrawler(desc)
            await crawler.__aenter__()
            self._program_crawlers[desc.code] = crawler
        else:
            crawler = maybe_crawler

        return crawler

    async def crawl_program(self, desc: ProgramDescription) -> Program:
        crawler = await self._get_program_crawler(desc)
        return await crawler.crawl()

    async def crawl_programs(self, descriptions: list[ProgramDescription]) -> list[Program]:
        programs: list[Program] = []
        start = time.perf_counter()
        for desc in descriptions:
            log = self._log.bind(program_name=desc.name, program_code=desc.code)

            program = await self.crawl_program(desc)
            programs.append(program)
            log.debug("Finished crawling a program.")

        end = time.perf_counter()
        elapsed_secs = end - start
        logger.info("Successfully finished crawling several programs.", took_secs=round(elapsed_secs, 3), crawled_count=len(descriptions))

        return programs

    async def crawl_course_by_url(
        self,
        *,
        url: URL,
        primary_program_desc: ProgramDescription,
    ) -> Course:
        print(url.parts)
        print((BASE_KUSSS_URL / ALL_COURSE_CLASSES_PAGE).parts)
        url_query_keys_set = set(url.query.keys()) 
        if not (
            url.parts == (BASE_KUSSS_URL / ALL_COURSE_CLASSES_PAGE).parts
            and (
                {"courseclassid"} <= url_query_keys_set
                or {"courseclass"} <= url_query_keys_set 
            )
        ):
            raise ValueError("Invalid KUSSS course page")

        program_crawler = await self._get_program_crawler(primary_program_desc)

        course_id = url.query.get("courseclassid")
        if course_id is None:
            course_id = url.query.get("courseclass")

        assert course_id is not None

        course = await program_crawler.course_by_course_id(course_id)
        return course
