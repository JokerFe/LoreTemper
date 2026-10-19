from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextEvidence(Strict):
    text: str | None
    frame_id: str
    uncertainty: str = ""


class Observation(Strict):
    description: str
    before_state: str = ""
    after_state: str = ""
    visible_texts: list[TextEvidence] = []
    frame_ids: list[str]
    candidate_actions: list[str] = []
    uncertainties: list[str] = []
    needs_crop: bool = False


class Parameter(Strict):
    name: str
    value: str | None
    evidence_ids: list[str]
    uncertainty: str = ""


class Step(Strict):
    title: str
    action: str
    before_state: str = ""
    after_state: str = ""
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    evidence_ids: list[str] = Field(min_length=1)
    evidence_type: Literal["visual", "speech", "both", "inferred"]
    parameters: list[Parameter] = []
    uncertainties: list[str] = []


class Chapter(Strict):
    title: str
    summary: str
    steps: list[Step]
    unresolved_items: list[str] = []


class Point(Strict):
    text: str
    evidence_ids: list[str] = Field(min_length=1)


class Overview(Strict):
    title: str
    summary: str
    summary_evidence_ids: list[str] = Field(min_length=1)
    goals: list[Point] = []
    prerequisites: list[Point] = []
    key_points: list[Point] = []
    verification: list[Point] = []
    unresolved_items: list[str] = []
