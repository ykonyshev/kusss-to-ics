from __future__ import annotations

import tomllib
from pathlib import Path

import structlog
from pydantic import BaseModel
from structlog import BoundLogger

logger: BoundLogger = structlog.get_logger()


class InvalidConfigError(Exception):
    """Error to indicate inconsistencies in the configuration file."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ConfigProgram(BaseModel):
    curriculum_pdf: Path


class Config(BaseModel):
    programs: list[ConfigProgram]

    @classmethod
    def from_file(cls, path: Path) -> Config:
        log = logger.bind(config_path=str(path.resolve()))

        with path.open("rb") as handle:
            raw_config = tomllib.load(handle)

        log.debug("Loaded raw config data.")
        config = cls.model_validate(raw_config)
        log.info("Validated and loaded config.")

        return config
