"""Pydantic models for file-based skill definitions."""
from typing import List, Optional
from pydantic import BaseModel


class SkillInput(BaseModel):
    name: str
    type: str
    description: str
    required: bool = False


class FileSkill(BaseModel):
    name: str
    engine: str
    script_path: str
    description: Optional[str] = None
    inputs: List[SkillInput] = []
    # Internal path info
    skill_dir: str
