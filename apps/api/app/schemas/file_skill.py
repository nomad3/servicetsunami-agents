"""Schemas for file-based skills."""
from pydantic import BaseModel
from typing import List, Optional


class SkillInput(BaseModel):
    name: str
    type: str = "string"
    description: str = ""
    required: bool = False


class FileSkill(BaseModel):
    name: str
    engine: str = "python"
    script_path: str = "script.py"
    description: Optional[str] = None
    inputs: List[SkillInput] = []
    skill_dir: str = ""
    # v2 fields
    version: int = 1
    category: str = "general"
    tags: List[str] = []
    auto_trigger: Optional[str] = None
    chain_to: List[str] = []
    prompts: List[str] = []
    tier: str = "native"
    slug: str = ""
    source_repo: Optional[str] = None
    tool_class: Optional[str] = None
