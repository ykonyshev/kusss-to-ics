from __future__ import annotations

import datetime
import re
import time
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import date
from types import TracebackType
from typing import Final, Self, Unpack

from aiohttp import ClientResponse, ClientSession
from aiohttp.client import _RequestOptions
from bs4 import BeautifulSoup, Tag
from pytz import timezone
from structlog import BoundLogger, get_logger
from yarl import URL

from kusss_to_ics.models.course import (
    COURSE_TYPES_REMAP,
    STRING_TO_MODE,
    Appointment,
    Appointments,
    Class,
    Course,
    Courses,
    Instructor,
    Mode,
)
from kusss_to_ics.models.program import Program, ProgramDescription
from kusss_to_ics.task_group_utils import async_map_collect

logger: BoundLogger = get_logger()

STATUS_OK: Final = 200

TIME_RANGE_SEP: Final = "–"
DATE_FORMAT: Final = "%d.%m.%y"
TIME_FORMAT: Final = "%H:%M"
EUROPE_VIENNA_TZ: Final = timezone("Europe/Vienna")

# TODO: Do rate limiting not to DDOS the KUSSS servers. Inspiration: https://stackoverflow.com/questions/48682147/aiohttp-rate-limiting-parallel-requests


class CrawlStatusError(RuntimeError):
    """Error to wrap HTTP status code failures that occured at crawl time."""

    def __init__(self, *, status: int, response: ClientResponse) -> None:
        super().__init__(f"Got non-{STATUS_OK} status code upon request.", status, response)


class MissingTagError(RuntimeError):
    """Error to reprent a state, where a tag that was expected to be present on the page could not be found."""

    def __init__(
        self,
        *,
        page_url: URL,
        related_tag: str | None = None,
        context: str | None = None,
        selector: str | None = None,
    ) -> None:
        super().__init__(
            "Could not find an element on the page.",
            page_url.human_repr(),
            related_tag,
            context,
            selector
        )


class MissingAttrError(RuntimeError):
    """Error to represent an attribute that was expected to be present on an tag being absent."""

    def __init__(self, *, tag: Tag, attr_name: str) -> None:
        super().__init__("Expected attribute not found on a tag.", tag, attr_name)


BASE_KUSSS_URL: Final = URL("https://www.kusss.jku.at/kusss/")

# Curriculum-related pages
COURSE_SEARCH_CATALOGUE_PAGE: Final = "coursecatalogue-start.action"
CURRICULUM_SPECIFIC_COURSE_CATALOGUE_PAGE: Final = "coursecatalogue-get-currbranches-or-segments.action"

# Course-related pages
COURSE_CLASSES_GROUP_PAGE: Final = "coursecatalogue-get-courseclasses.action"
ALL_COURSE_CLASSES_PAGE: Final = "lvaregistrationlist.action"
SPECIFIC_COURSE_CLASS_PAGE: Final = "lvaregistrationlist.action"


class ProgramCrawler(AbstractAsyncContextManager):
    """The class containing the crawling logic to retrieve information about the
    classes in a given curriculum by the program code.

    ## Hierarchy

    The object hierarchy in KUSSS is as follows:

    - The course search page has a dropdown containing all the curricula that
    can be found in the university, a specific curriculum is uniquely
    indentified on the university level given the program code, that can also be
    located in the respective curriculum PDF file. The page with all curricula
    has the dropdown where a specific curriculum can be selected that also
    contains the KUSSS curriculum ID that is used internally by KUSSS to
    uniquely refrence a specific curriculum.
    - Each curriculum has a page containing the full list of mandatory courses
    as well as area of specialization courses for that specific curriculum that
    is similar to the respective page on studienhandbuch.jku.at, but specific to
    KUSSS with links to the courses embedded as well. The links on the page link
    to so-called course groups. To retrieve the list of class groups that
    correspond to the courses of a given curriculum one has to provide the KUSSS
    curriculum ID.
    - In curricula there are the courses and each course seems to have a 1-to-1
    correspondance to some so-called class group, that's how the courses seem to
    be represented in KUSSS. The curriculum course list page links directly to
    the specific class groups, no intermediate page for all class groups for a
    given course seems to exist. To retrieve a specific class group one has to
    provide the KUSSS curriculum ID as well as the course group ID.
    - From the course page that corresponds to the course class group page a
    list of all classes for that course can be retrieved. Each course is
    indentified by a KUSSS "course (class) ID".
    - Each class in a course has a separate link that can used to retrieve the
    list of appointments for that class. The course ID as well as the class code
    (for some reason the column header on KUSSS is called "Course ID" instead of
     something like "Class ID") has to be provided.

    ## Summary

    Format: <object name> (<idenfitiers>) -> <related object> (<idenfitiers>) -> ...

        curriculum (program code -> curriculum KUSSS ID)
        -> courses <-> course class group (course class group KUSSS ID)
        -> classes (class ID)
        -> appointments (class ID and class code)
    """

    def __init__(self, program: ProgramDescription) -> None:
        self._program = program
        self._session = ClientSession(BASE_KUSSS_URL)
        self._session.cookie_jar.update_cookies({
            "language": program.language
        })

    async def __aenter__(self) -> Self:
        await self._session.__aenter__()

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /
    ) -> None:
        await self._session.__aexit__(
            exc_type,
            exc_value,
            traceback
        )

    @asynccontextmanager
    async def get(
        self,
        url: URL,
        **kwargs: Unpack[_RequestOptions],
    ) -> AsyncGenerator[tuple[ClientResponse, BoundLogger]]:
        log = logger.bind(url=url.human_repr())

        log.debug("GET request.")
        start = time.perf_counter()
        async with self._session.get(url, **kwargs) as response:
            end = time.perf_counter()
            elapsed_secs = end - start

            log = log.bind(status_code=response.status)
            log.info("GET response.", took_s=round(elapsed_secs, 3))

            if response.status != STATUS_OK:
                raise CrawlStatusError(
                    status=response.status,
                    response=response
                )

            yield response, log

    async def get_soup(
        self,
        url: URL,
    ) -> BeautifulSoup:
        async with self.get(url) as (response, _):
            raw_html = await response.text()

        return BeautifulSoup(raw_html, "lxml")

    async def crawl(self) -> Program:
        curriculum_id = await self._curriculum_id_by_curriculum_code()
        if curriculum_id is None:
            raise RuntimeError(
                f"Could not retrieve a KUSSS curriculum ID for program with code: \"{self._program.code}\". Make sure that the the program code is correctly specified."
            )

        courses = await self._courses_by_curriculum_id(curriculum_id)
        return Program(
            name=self._program.name,
            code=self._program.code,
            courses=courses,
        )

    async def _curriculum_id_by_curriculum_code(self) -> str | None:
        """Extract the internal KUSSS curriculum ID given the program code.

        The KUSSS curriculum ID is extracted from the program selection dropdown on the course search page in KUSSS.
        """
        soup = await self.get_soup(BASE_KUSSS_URL / COURSE_SEARCH_CATALOGUE_PAGE)

        select_tag_selector = f"form[action=\"{CURRICULUM_SPECIFIC_COURSE_CATALOGUE_PAGE}\"] > select"
        select_tag = soup.select_one(select_tag_selector)
        if select_tag is None:
            raise MissingTagError(
                selector=select_tag_selector,
                page_url=BASE_KUSSS_URL / COURSE_SEARCH_CATALOGUE_PAGE,
            )

        matching_curriculum_id: str | None = None
        options = select_tag.select("option")
        if not options:
            raise MissingTagError(
                selector="option",
                page_url=BASE_KUSSS_URL / COURSE_SEARCH_CATALOGUE_PAGE
            )

        program_code_string = " ".join(self._program.code)
        for option in options:
            if program_code_string in option.text:
                value_attr = option.get("value")
                if value_attr is None:
                    raise MissingAttrError(
                        tag=option,
                        attr_name="value",
                    )

                matching_curriculum_id = str(value_attr)

        return matching_curriculum_id

    async def _courses_by_curriculum_id(self, curriculum_id: str) -> Courses:
        """Retrieve the courses by a given KUSSS curriculum ID."""
        url = (BASE_KUSSS_URL / CURRICULUM_SPECIFIC_COURSE_CATALOGUE_PAGE).extend_query({
            "curId": curriculum_id,
            "set.listsubjects-overview.treeView.expandAll": "true",
            "set.listsubjects-overview.treeView.expandedNodes": "",
        })

        soup = await self.get_soup(url)

        course_anchors_selector = "td td[align=\"left\"] a"
        course_anchors = soup.select(course_anchors_selector)
        if not course_anchors:
            raise MissingTagError(
                selector=course_anchors_selector,
                page_url=url
            )

        course_class_group_ids: list[str] = []
        for anchor in course_anchors:
            anchor_href = anchor.get("href")
            if anchor_href is None:
                raise MissingAttrError(
                    tag=anchor,
                    attr_name="href",
                )

            anchor_href_url = URL(str(anchor_href))
            course_class_group_id = anchor_href_url.query.get("grpCode")
            if course_class_group_id is None:
                raise RuntimeError(f"Could not get course class group code from the given URL: \"{anchor_href}\".")

            course_class_group_ids.append(course_class_group_id)

        courses = await async_map_collect(
            lambda course_group_id: self._course_by_respective_class_group(
                curriculum_id=curriculum_id,
                course_class_group_id=course_group_id,
            ),
            course_class_group_ids
        )

        return courses

    async def _course_by_respective_class_group(
        self,
        *,
        curriculum_id: str,
        course_class_group_id: str,
    ) -> Course:
        """Retrieve the course information given the curriculum ID as well as
        the corresponding course class group ID.

        The curriculum ID and the course class group ID is what identifies a
        given course class group that corresponds 1-to-1 to a course in a
        curriculum.
        """
        url = (BASE_KUSSS_URL / COURSE_CLASSES_GROUP_PAGE).extend_query({
            "curId": curriculum_id,
            "segId": 1,
            "grpCode": course_class_group_id,
        })
        soup = await self.get_soup(url)
        course_anchors_selector = "td strong a"
        course_anchors = soup.select(course_anchors_selector)
        if not course_anchors:
            raise MissingTagError(
                selector=course_anchors_selector,
                page_url=url,
            )

        if len(course_anchors) > 1:
            logger.warning("Found more than one course anchor on the page.")
    
        anchor = course_anchors[0]
        anchor_href = anchor.get("href")
        if anchor_href is None:
            raise MissingAttrError(
                tag=anchor,
                attr_name="href",
            )

        anchor_href_url = URL(str(anchor_href))
        course_id = anchor_href_url.query.get("courseclass")
        if course_id is None:
            raise RuntimeError(f"Could not get course class ID from the given URL: \"{anchor_href}\".")

        return await self.course_by_course_id(
            course_id=course_id,
        )

    async def course_by_course_id(self, course_id: str) -> Course:
        """Retrieve the course information given the course ID."""
        url = (BASE_KUSSS_URL / ALL_COURSE_CLASSES_PAGE).extend_query({
            "courseclass": course_id
        })
        soup = await self.get_soup(url)

        no_lectures_message_p = soup.select_one("p.message")
        if no_lectures_message_p is not None:
            classes = []
        else:
            course_code_anchors_selector = "table tr td strong a"
            course_code_anchors = soup.select(course_code_anchors_selector)
            if not course_code_anchors:
                raise MissingTagError(
                    selector=course_code_anchors_selector,
                    page_url=url,
                )

            course_class_ids_and_codes: list[tuple[str, str]] = []
            for anchor in course_code_anchors:
                anchor_href = anchor.get("href")
                anchor_href_url = URL(str(anchor_href))
                course_class_code = anchor_href_url.query.get("showdetails")
                course_class_id = anchor_href_url.query.get("courseclassid")

                if course_class_code is None or course_class_id is None:
                    raise RuntimeError(f"Could not get course class ID or the course class code from the given URL: \"{anchor_href}\".")

                course_class_ids_and_codes.append((course_class_id, course_class_code))

            classes = [
                class_
                for class_ in await async_map_collect(
                    self._class_by_course_id_and_class_code,
                    course_class_ids_and_codes
                )
                if class_ is not None
            ]

        header_selector = "td h3"
        header = soup.select_one(header_selector)
        if header is None:
            raise MissingTagError(
                selector=header_selector,
                page_url=url,
            )

        course_name = re.sub(r"\s+", " ", header.text.partition(":")[-1].partition("[")[0].strip())
        course_type_abbr = header.select_one("abbr")
        assert course_type_abbr is not None

        course_type = COURSE_TYPES_REMAP.get(course_type_abbr.text.strip())
        if course_type is None:
            raise RuntimeError(f"Unknown course type: {course_type}.")

        header_container = header.parent
        assert header_container is not None

        ects_str = header_container.text.partition("ECTS:")[-1].partition("|")[0].strip().replace(",", ".")

        return Course(
            name=course_name,
            type_=course_type,
            ects=float(ects_str),
            classes=classes
        )

    async def _class_by_course_id_and_class_code(self, course_id: str, class_code: str) -> Class | None:
        """Retrieve the class infomration given the course ID as well as the
        class code.

        The course ID and the class code is what uniquely identifies a specific
        class object.
        """
        url = (BASE_KUSSS_URL / SPECIFIC_COURSE_CLASS_PAGE).extend_query({
            "showdetails": class_code,
            "courseclassid": course_id,
            "abhart": "all",
        })
        soup = await self.get_soup(url)

        class_trs_selector = "table tr:has(a)"
        matching_tr: Tag | None = None
        class_trs = soup.select(class_trs_selector)
        if not class_trs:
            raise MissingTagError(
                selector=class_trs_selector,
                page_url=url,
            )

        for tr in class_trs:
            class_anchor = tr.select_one("a")
            if class_anchor is None:
                continue

            class_href = class_anchor.get("href")
            if class_href is None:
                continue

            class_href_url = URL(str(class_href))
            if class_href_url.query.get("courseclass") == course_id:
                matching_tr = tr

        if matching_tr is None:
            # FIXME: Support crawling courses that are also already open for
            # registration
            logger.warning("The course has already opened for registration, parsing it is not yet supported.")
            return None

        instructor_td = matching_tr.select_one("td:nth-child(5)")
        mode_td = matching_tr.select_one("td:last-child")

        if instructor_td is None or mode_td is None:
            raise MissingTagError(
                selector="td",
                page_url=url,
            )

        mode_string = mode_td.text.strip()

        instructor_names = list(map(str.strip, instructor_td.text.split(",")))
        instructors = [
            Instructor(name=name)
            for name in instructor_names
        ]

        additional_info = None
        additional_info_tr = matching_tr.find_next("tr", class_="priorityhighlighted")
        if additional_info_tr is not None:
            additional_info = additional_info_tr.text.strip()

        details_anchor = matching_tr.find_next("a", id="details")
        if details_anchor is None:
            raise MissingTagError(
                selector="a.details",
                page_url=url,
            )

        details_table = details_anchor.find_next("table", class_="subinfo")
        if details_table is None:
            raise MissingTagError(
                selector="table",
                page_url=url,
            )

        appointments_trs_iter = iter(details_table.select("table tr:has(td):not(table tr img)"))
        next(appointments_trs_iter)  # skipping the export button tr

        appointments: Appointments = []
        for appointment_tr in appointments_trs_iter:
            tds = appointment_tr.select("td")
            if len(tds) < 4:
                continue

            _, date_td, time_range_td, room_td = tds
            appointment_date = date.strptime(date_td.text.strip(), DATE_FORMAT)
            appointment_from_str, _, appointment_to_str = map(str.strip, time_range_td.text.partition(TIME_RANGE_SEP))
            appointment_from, appointment_to = (
                datetime.time.strptime(appointment_from_str, TIME_FORMAT).replace(tzinfo=EUROPE_VIENNA_TZ),
                datetime.time.strptime(appointment_to_str, TIME_FORMAT).replace(tzinfo=EUROPE_VIENNA_TZ),
            )

            room_str = room_td.text.strip()
            appointment = Appointment(
                time_range=(appointment_from, appointment_to),
                date=appointment_date,
                room=room_str
            )

            appointments.append(appointment)
            
        return Class(
            code=f"{class_code[:3]}.{class_code[3:]}",
            mode=STRING_TO_MODE.get(mode_string.lower(), Mode.OTHER),
            instructors=instructors,
            kusss_page=url,
            appointments=appointments,
            additional_info=additional_info,
        )
