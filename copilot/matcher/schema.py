from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Canonical skill names: the model is told to use them, and we enforce them
# here too, so "Py", "python3" and "Python" all count as one skill in analytics.
SKILL_ALIASES = {
    "py": "python", "python3": "python", "js": "javascript", "ts": "typescript",
    "postgres": "postgresql", "k8s": "kubernetes", "gcp": "google cloud",
    "ml": "machine learning", "llms": "llm", "large language models": "llm",
    "rag": "retrieval-augmented generation", "nlp": "natural language processing",
    "sklearn": "scikit-learn", "aws cloud": "aws",
    "powerbi": "power bi", "ms power bi": "power bi", "ms excel": "excel", "advanced excel": "excel",
    "microsoft excel": "excel", "advanced sql": "sql", "ms sql": "sql", "pyspark": "spark",
    "a/b testing": "ab testing", "statistics": "statistical analysis",
    "llm apis": "llm", "llm api": "llm", "openai api": "llm", "generative ai": "llm", "genai": "llm",
    "prompting": "prompt engineering", "stats": "statistical analysis",
}


# Industry names vary by portal and by model; map common variants to one name so that
# "food & beverage", "consumer goods" and "CPG" all match a profile that says "fmcg".
DOMAIN_ALIASES = {
    "cpg": "fmcg", "consumer packaged goods": "fmcg", "consumer goods": "fmcg", "food": "fmcg",
    "food and beverage": "fmcg", "food & beverage": "fmcg", "f&b": "fmcg", "beverages": "fmcg",
    "fast moving consumer goods": "fmcg", "personal care": "fmcg",
    "e-commerce": "ecommerce", "e commerce": "ecommerce", "online retail": "ecommerce",
    "banking": "bfsi", "financial services": "bfsi", "fintech": "bfsi", "insurance": "bfsi", "finance": "bfsi",
    "healthcare": "health", "pharma": "health", "pharmaceuticals": "health",
    "pricing analytics": "pricing", "revenue management": "pricing", "promotional analytics": "promotions",
    "loyalty programs": "loyalty", "loyalty data": "loyalty", "customer analytics": "consumer analytics",
}


def canonical_domain(d: str) -> str:
    d = " ".join(d.strip().lower().split())
    return DOMAIN_ALIASES.get(d, d)


def domains_match(job_domain: str, profile_domain: str) -> bool:
    """Same canonical domain, or one's words contained in the other's ('retail' ~ 'retail analytics').
    Whole words only: 'ai' must not match 'retail'."""
    a, b = canonical_domain(job_domain), canonical_domain(profile_domain)
    if a == b:
        return True
    wa, wb = set(a.split()), set(b.split())
    return bool(wa) and bool(wb) and (wa <= wb or wb <= wa)


def canonical_skill(s: str) -> str:
    s = " ".join(s.strip().lower().split())
    return SKILL_ALIASES.get(s, s)


class JobPosting(BaseModel):
    """Structured fields of one job posting. Use null when the posting does not say."""

    title: str = Field(description="Job title as written, without company name")
    company: str | None = None
    location: str | None = Field(None, description="City/region, or null if fully remote with no location")
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    seniority: Literal["intern", "junior", "mid", "senior", "lead", "unknown"] = "unknown"
    min_years_experience: int | None = Field(None, ge=0, le=40)
    salary_min: int | None = Field(None, ge=0, description="Annualised if period is year; whole currency units")
    salary_max: int | None = Field(None, ge=0)
    salary_currency: str | None = Field(None, pattern=r"^[A-Z]{3}$", description="ISO 4217, e.g. USD, INR")
    salary_period: Literal["year", "month", "hour"] | None = None
    required_skills: list[str] = Field(default_factory=list, max_length=25)
    nice_to_have_skills: list[str] = Field(default_factory=list, max_length=25)
    visa_sponsorship: bool | None = None
    domains: list[str] = Field(default_factory=list, max_length=8,
                               description="Industries/business areas, lowercase: e.g. fmcg, retail, pricing, banking")

    @field_validator("domains")
    @classmethod
    def _normalise_domains(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(canonical_domain(d) for d in v if d.strip()))

    @field_validator("required_skills", "nice_to_have_skills")
    @classmethod
    def _normalise_skills(cls, v: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for s in v:
            c = canonical_skill(s)
            if c:
                seen.setdefault(c, None)
        return list(seen)

    @model_validator(mode="after")
    def _salary_consistent(self) -> "JobPosting":
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("salary_min must be <= salary_max")
        has_amount = self.salary_min is not None or self.salary_max is not None
        if has_amount and (self.salary_currency is None or self.salary_period is None):
            raise ValueError("salary_currency and salary_period are required when a salary amount is given")
        # A skill can't be both required and optional; required wins.
        self.nice_to_have_skills = [s for s in self.nice_to_have_skills if s not in self.required_skills]
        return self
