"""Typed contracts passed between the coach's agents (the 'handoffs')."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Stage = Literal["response", "assessment", "interview", "offer", "insufficient_data"]


class Diagnosis(BaseModel):
    """Evidence-based diagnosis of where the job search stalls. Every finding must quote numbers from tool results."""
    findings: list[str] = Field(min_length=1, max_length=6,
                                description="Each finding states a fact with the numbers that prove it")
    stalls_at: Stage = Field(description="Stage with the biggest drop-off: response (applied->any reply), "
                                         "assessment (reply->interview), interview (interview->offer)")
    strongest_segment: str | None = Field(None, description="Portal or role family converting best, e.g. 'LinkedIn'")
    weakest_segment: str | None = Field(None, description="Portal or role family converting worst")
    top_skill_gaps: list[str] = Field(default_factory=list, max_length=5,
                                      description="In-demand skills the candidate lacks, from the skill_gaps tool")


class LearningItem(BaseModel):
    skill: str
    hours: float = Field(gt=0, le=20)
    why: str = Field(description="Which finding or gap this addresses")
    deliverable: str = Field(description="Concrete proof of learning, e.g. 'Power BI dashboard on GitHub'")


class WeeklyPlan(BaseModel):
    """One week of job-search work targeted at the diagnosed bottleneck."""
    focus_roles: list[str] = Field(min_length=1, max_length=3, description="Role families to prioritise")
    focus_sources: list[str] = Field(default_factory=list, max_length=3, description="Portals to prioritise")
    applications_target: int = Field(ge=1, le=60, description="Tailored applications to send this week")
    learning: list[LearningItem] = Field(default_factory=list, max_length=4)
    actions: list[str] = Field(min_length=1, max_length=6, description="Specific actions for the bottleneck stage")
    rationale: str = Field(max_length=600)
