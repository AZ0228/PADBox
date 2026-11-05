"""Validated interchange contracts. Similarity is never a calibrated probability."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Provenance(BaseModel):
    source: str
    version: str
    url: str | None = None
    fetched_at: str | None = None
    updated_at: str | None = None
    sha256: str | None = None
    usage: str | None = None


class Skill(BaseModel):
    skill_id: int
    name: str
    description: str = ""
    base_cooldown: int | None = None
    minimum_cooldown: int | None = None
    effects: list[dict[str, Any]] = Field(default_factory=list)
    evidence: Literal["unknown", "source_encoded", "verified", "heuristic"] = "unknown"
    raw: dict[str, Any] = Field(default_factory=dict)


class Relationship(BaseModel):
    target_id: int = Field(gt=0)
    kind: Literal["evolution", "assist_evolution", "transform"]
    evidence: str = "source_encoded"


class Monster(BaseModel):
    monster_id: int = Field(gt=0)
    name: str
    servers: list[str] = Field(default_factory=list)
    attributes: list[int] = Field(default_factory=list)
    types: list[int] = Field(default_factory=list)
    rarity: int | None = None
    icon: Path | None = None
    provenance: Provenance
    active_skill: Skill | None = None
    leader_skill: Skill | None = None
    awakenings: list[int] = Field(default_factory=list)
    super_awakenings: list[int] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class Candidate(BaseModel):
    monster_id: int
    name: str
    score: float = Field(ge=0, le=1)
    reference_icon: str
    components: dict[str, float] = Field(default_factory=dict)


class Match(BaseModel):
    candidates: list[Candidate]
    high_confidence: bool = False
    reason: str


class Layout(BaseModel):
    """All coordinates normalized to screen dimensions. Forced rows require alignment."""

    roi: tuple[float, float, float, float] = (0, 0, 1, 1)
    columns: int | None = Field(default=None, ge=3, le=12)
    x: float | None = None
    y: float | None = None
    size: float | None = None  # Fraction of screen WIDTH, preserves square cells.
    step_x: float | None = None
    step_y: float | None = None  # Fraction of screen HEIGHT.
    follow_scroll: bool = False  # Estimate the row phase from repeated icon border edges.

    @model_validator(mode="after")
    def validate_geometry(self) -> "Layout":
        x1, y1, x2, y2 = self.roi
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("ROI must be normalized x1,y1,x2,y2 within [0,1]")
        values = [self.x, self.y, self.size, self.step_x, self.step_y]
        if any(v is not None for v in values):
            if not all(v is not None for v in values) or self.columns is None:
                raise ValueError("Forced grid requires x,y,size,step_x,step_y,columns")
            if not all(0 <= v <= 1 for v in values if v is not None):
                raise ValueError("Grid coordinates must be within [0,1]")
            if any(v <= 0 for v in [self.size, self.step_x, self.step_y]):
                raise ValueError("Grid size and steps must be positive")
        if self.follow_scroll and self.size is None:
            raise ValueError("Scroll-following requires calibrated grid geometry")
        return self
