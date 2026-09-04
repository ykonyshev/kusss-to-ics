import re
from pathlib import Path
from typing import Final, cast

import camelot.io
import numpy as np
import pandas as pd
from attrs import define
from pandas import DataFrame, MultiIndex, Series
from pandas.api.typing import NAType
from pdfminer.high_level import extract_pages

from kusss_to_ics.models.course import COURSE_TYPES, COURSE_TYPES_REMAP, CourseType

COURSE_NAMES_REGEX: Final = re.compile(rf"(\S.*?)\s\((\d+\.?\d*)\s({"|".join(COURSE_TYPES)})\)")
NON_MANDATORY_COURSE_NAMES: Final = {"Area of Specialization", "Free Electives"}


type CourseData = tuple[str, float | NAType, str | NAType]

def split_course_strings(col: Series) -> Series:
    def process_cell(cell: str, course_groups: set[str]) -> list[CourseData]:
        if not cell:
            return []

        prepared = cell.replace("\n", "").strip()
        for group in course_groups:
            start_idx = prepared.find(group)
            if start_idx == -1:
                continue

            after_match = start_idx + len(group)
            if after_match == len(prepared):
                break

            prepared = prepared[after_match:].strip()
            break

        courses: list[CourseData] = []

        matches = list(COURSE_NAMES_REGEX.finditer(prepared))
        if not matches:
            if prepared:
                return [(prepared, pd.NA, pd.NA)]

            return []

        for match in matches:
            course_name, ects_str, course_type = cast(tuple[str, str, str], match.groups())
            ects = float(ects_str)

            courses.append((course_name, ects, course_type))

        return courses

    course_lines = col.str.split("\n").to_list()
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

    return col.map(lambda v: process_cell(v, course_groups)).explode()


# TODO: Make the code more generic where it also works with other curricula: TM,
# CS. Or write separate parsers that are selected dynamically based on the
# program code for a given curriculum PDF (provided on the title page).
def parse_curriculum(pdf_path: Path) -> DataFrame:
    pages = list(extract_pages(pdf_path))
    page_count = len(pages)

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
            .assign(is_mandatory=lambda df: ~df["course_name"].isin(NON_MANDATORY_COURSE_NAMES))
    )

    return courses_df


@define
class CourseDescription:
    name: str
    ects: float
    type_: CourseType


type CourseDescriptions = list[CourseDescription]


def curriculum_df_to_course_descriptions(df: DataFrame) -> CourseDescriptions:
    courses: CourseDescriptions = []
    for _, row in df.iterrows():
        course_type = COURSE_TYPES_REMAP.get(cast(str, row["course_type"]))
        if course_type is None:
            raise ValueError("Received unknown course type from the data frame.")

        course = CourseDescription(
            name=cast(str, row["course_name"]),
            type_=course_type,
            ects=cast(float, row["ects"]),
        )

        courses.append(course)

    return courses
