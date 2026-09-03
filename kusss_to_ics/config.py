from __future__ import annotations

import tomllib
from pathlib import Path

import structlog
from pydantic import BaseModel
from structlog import BoundLogger

from kusss_to_ics.models.program import Program

logger: BoundLogger = structlog.get_logger()


class Config(BaseModel):
    programs: list[Program]

    @classmethod
    def from_file(cls, path: Path) -> Config:
        log = logger.bind(config_path=str(path.resolve()))

        with path.open("rb") as handle:
            raw_config = tomllib.load(handle)

        log.debug("Loaded raw config data.")
        config = cls.model_validate(raw_config)
        log.info("Validated and loaded config.")

        return config

