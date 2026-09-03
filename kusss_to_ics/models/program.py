from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class Program(BaseModel):
    name: str
    code: str
    curriculum_pdf: Path
