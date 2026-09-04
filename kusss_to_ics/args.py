from __future__ import annotations

import os
from argparse import ArgumentError, ArgumentParser, Namespace
from pathlib import Path
from typing import Final

CONFIG_FILE_SUFFIX: Final = ".toml"
CONFIG_FILE_REQUIREMENTS_DESC: Final = f"The provide path must be a path to an existing and readable *{CONFIG_FILE_SUFFIX} file."


class Args(Namespace):
    config_file: Path
    semester: int
    export_path: Path
    split_by_course_type: bool

    @classmethod
    def parse(cls) -> Args:
        parser = ArgumentParser()
        config_file_action = parser.add_argument(
            "config_file",
            type=Path,
            help=f"Path to the configuration file. {CONFIG_FILE_REQUIREMENTS_DESC}",
        )

        export_path_action = parser.add_argument(
            "export_path",
            type=Path,
            help="Export file, must be an `*.ics` file.",
        )

        parser.add_argument(
            "--semester",
            "-s",
            type=int,
            help="The semester number (starting from 1) for which to consider the mandatory courses.",
            required=True,
            dest="semester",
        )

        parser.add_argument(
            "--split-by-course-type",
            action="store_true",
            help="When passed, multiple `*.ics` files are generated, separate for each course type.",
        )

        args = cls()
        parser.parse_args(namespace=args)

        if not (args.config_file.is_file() and args.config_file.suffix == CONFIG_FILE_SUFFIX and os.access(args.config_file, os.R_OK)):
            raise ArgumentError(config_file_action, f"Invalid config file path. {CONFIG_FILE_REQUIREMENTS_DESC}")

        if args.split_by_course_type and not (not args.export_path.exists() or args.export_path.is_dir()):
            raise ArgumentError(export_path_action, "If the `--split-by-course-type` flag is provided, the export path must either be a path to non-existing file system object, or a path an existing directory.")

        return args
