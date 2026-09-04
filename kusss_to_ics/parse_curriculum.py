from __future__ import annotations

import re
import string
from pathlib import Path
from typing import Final, cast

import camelot.io
import numpy as np
import pandas as pd
import pdfminer.high_level
import structlog
from attrs import define
from pandas import DataFrame, MultiIndex, Series
from pandas.api.typing import NAType
from pdfminer.layout import LTTextBox
from structlog import BoundLogger

from kusss_to_ics.export_ics import CourseExportDescription
from kusss_to_ics.models.course import COURSE_TYPES, COURSE_TYPES_REMAP, CourseType
from kusss_to_ics.models.program import (
    LanguageCode,
    ProgramCode,
    ProgramDescription,
    parse_program_code,
)

logger: BoundLogger = structlog.get_logger()

_COURSE_TYPES_OR: Final = "|".join(COURSE_TYPES)
COURSE_STRING_REGEX: Final = re.compile(rf"(\S.*?)\s\((\d+(?:\.|,)?\d*)\s(?:({_COURSE_TYPES_OR})|(?:ECTS))\)")
NON_MANDATORY_COURSE_NAMES: Final = {"Area of Specialization", "Free Electives", "Wahlfächer", "Freie Studienleistung"}
GERMAN_KEY_STRING: Final = "bachelorstudium"
SKIP_COURSES: Final = {"Gender Studies"}

type CourseData = tuple[str, float | NAType, str | NAType]


def split_course_strings(col: Series) -> Series:
    log = logger.bind()
    def process_cell(cell: str, course_groups: set[str]) -> list[CourseData]:
        if not cell:
            return []

        if ")" in cell:
            # AI curriculum has the ECTS counts in the parentheses
            prepared = cell.replace("\n", "")
        else:
            # other don't...
            prepared = cell

        prepared = prepared.strip().replace(" \n", " ")
        for group in course_groups:
            if not prepared.startswith(group):
                continue

            start_idx = prepared.find(group)
            if start_idx == -1:
                continue

            after_match = start_idx + len(group)
            if after_match == len(prepared):
                break

            prepared = prepared[after_match:].strip()
            break

        prepared = prepared.strip("– ")
        courses: list[CourseData] = []

        prepared_lines = prepared.splitlines()
        for prepared in prepared_lines:
            if prepared in SKIP_COURSES:
                continue

            matches = list(COURSE_STRING_REGEX.finditer(prepared))

            if not matches:
                if prepared:
                    return [(prepared, pd.NA, pd.NA)]

                return []

            for match in matches:
                course_name, ects_str, course_type = cast(tuple[str, str, str], match.groups())
                ects_str = ects_str.replace(",", ".")
                ects = float(ects_str)

                if course_name in SKIP_COURSES:
                    continue

                courses.append((course_name, ects, course_type))

        return courses

    course_lines = col.str.replace(" \n", " ").str.split("\n").to_list()
    course_groups = set()
    for line_idx, line in enumerate(course_lines):
        if not line or not line[0]:
            continue

        for other_idx, other_line in enumerate(course_lines):
            if line_idx == other_idx:
                continue

            i = 0
            if len(line) >= 2 and line[1].startswith(line[0]):
                i = 1
            else:
                while i < len(line) and i < len(other_line) and line[i] == other_line[i]:
                    i += 1

            if i > 0:
                course_group = "".join(line[:i])
                course_groups.add(course_group)

    log.debug("Found course group in the curriculum.", course_groups=course_groups)

    return col.map(lambda v: process_cell(v, course_groups)).explode()


PROGRAM_NAME_TRANSLATION_TABLE: Final = str.maketrans({
    "’": "'"
})

type CoursesDataFrame = DataFrame


def parse_curriculum(pdf_path: Path) -> tuple[ProgramDescription, CoursesDataFrame]:
    log = logger.bind(curriculum_pdf=str(pdf_path.resolve()))

    pages = list(pdfminer.high_level.extract_pages(pdf_path))
    page_count = len(pages)

    program_code: ProgramCode | None = None
    title_page = pages[0]

    program_code_object, program_name_object, *_ = [
        obj
        for obj in title_page
        if isinstance(obj, LTTextBox)
    ]

    text = program_code_object.get_text().strip()
    program_code = parse_program_code(text)
    if program_code is None:
        error_message = "Could not extract the program code from the curriculum PDF."
        log.error(error_message)
        raise RuntimeError(f"{error_message} Curriculum PDF: {pdf_path.resolve()!s}.")

    program_name = " ".join(map(str.capitalize, " ".join(program_name_object.get_text().strip().split("\n")[-2:]).strip(string.whitespace + ".").translate(PROGRAM_NAME_TRANSLATION_TABLE).split(" ")))
    language: LanguageCode = "de" if GERMAN_KEY_STRING in program_name.lower() else "en"

    program = ProgramDescription(
        name=program_name,
        code=program_code,
        language=language,
    )

    tables = camelot.io.read_pdf(pdf_path, pages=f"{page_count}-{page_count}")
    if len(tables) != 1:
        raise ValueError("None at all or more than one table is present on the last page of the provided PDF for the curriculum.")

    table = tables[0]
    df = table.df

    sem_count = df.shape[1] // 2

    df = (
        df
            .set_axis(
                MultiIndex.from_arrays([
                    np.repeat([f"{num}_sem" for num in range(1, sem_count + 1)], 2),
                    np.tile(["course", "ects"], reps=sem_count)
                    
                ]),
                axis=1,
            )
            .drop([0, 1])
            .reset_index(drop=True)
            .stack(level=0)
            .assign(ects=lambda df: pd.to_numeric(df["ects"].str.replace(",", "."), downcast="float"))
            .reset_index(level=1)
            .rename(columns={"level_1": "sem"})
            .assign(sem=lambda df: pd.to_numeric(df["sem"].str.partition("_")[0], downcast="unsigned"))
            .reset_index(drop=True)
            .astype({
                "course": "string",
            })
    )

    courses_data = split_course_strings(cast(Series, df["course"]))
    courses_df = (
        DataFrame(
            courses_data.to_list(),
            columns=["course_name", "ects", "course_type"],
            index=courses_data.index,
        )
            .astype({
                "course_name": "string",
                "ects": "Float32",
                "course_type": "category"
            })
            .assign(ects=lambda df_: df_["ects"].fillna(df["ects"]))
            .join(df[["sem"]].astype({"sem": "UInt8"}))
            .dropna(ignore_index=True, how="all", subset=["course_name", "ects"])
            .dropna(ignore_index=True, subset=["course_name"])
            .assign(is_mandatory=lambda df: ~df["course_name"].isin(NON_MANDATORY_COURSE_NAMES))
    )

    return program, courses_df


@define
class CourseDescription:
    name: str
    ects: float | NAType = pd.NA
    type_: CourseType | NAType = pd.NA


type CourseDescriptions = list[CourseDescription]


def courses_df_to_course_export_descriptions(df: DataFrame) -> list[CourseExportDescription]:
    courses: list[CourseExportDescription] = []
    for _, row in df.iterrows():
        course_type = cast(str | float, row["course_type"])
        if pd.isna(course_type):
            course_type = pd.NA
        else:
            course_type = COURSE_TYPES_REMAP.get(cast(str, course_type), pd.NA)

        course = CourseExportDescription(
            description=CourseDescription(
                name=cast(str, row["course_name"]),
                type_=course_type,
                ects=cast(float, row["ects"]),
            ),
            extraculicular=False,
        )

        courses.append(course)

    return courses
