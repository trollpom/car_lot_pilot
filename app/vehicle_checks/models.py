"""Explicit, bounded public contract. Unknown provider fields are not stored."""
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator

Mode = Literal["live", "demo"]
Status = Literal["ok", "empty", "partial", "unavailable"]
Level = Literal["low", "medium", "high", "critical", "unknown"]


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    query: str = Field(default="", max_length=40)
    plate: str = Field(default="", max_length=20)
    mode: Mode = "live"
    consent_to_external: StrictBool = False
    allow_paid: StrictBool = False


class SafeModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_max_length=2000)


class Vehicle(SafeModel):
    vin: str = Field(max_length=17, pattern=r"^[A-HJ-NPR-Z0-9]{17}$")
    plate: str = Field(default="", max_length=40)
    make: str = Field(max_length=160)
    model: str = Field(max_length=160)
    year: int = Field(ge=0, le=2100)


class Factor(SafeModel):
    title: str
    detail: str
    severity: Level
    source: str


class Summary(SafeModel):
    score: int | None = Field(default=None, ge=0, le=100)
    level: Level
    verdict: str
    headline: str
    confidence: Literal["none", "low", "partial", "full"]
    factors: list[Factor] = Field(default_factory=list, max_length=100)


class Block(SafeModel):
    id: str = Field(max_length=60)
    title: str = Field(max_length=160)
    status: Status
    note: str = ""
    sources: list[str] = Field(default_factory=list, max_length=40)


class Coverage(SafeModel):
    available: int = Field(ge=0, le=100)
    total: int = Field(ge=0, le=100)
    blocks: list[Block] = Field(max_length=100)


class Source(SafeModel):
    id: str = Field(max_length=60)
    name: str = Field(max_length=160)
    status: Status
    records: int = Field(ge=0)
    fetched_at: AwareDatetime
    via: str = Field(default="", max_length=160)
    note: str = ""


class Fact(SafeModel):
    id: str = Field(max_length=60)
    title: str = Field(max_length=160)
    status: Status
    value: str | None


class Report(SafeModel):
    contract: Literal["car-lot-pilot/v1"]
    id: str = Field(max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    created_at: AwareDatetime
    expires_at: AwareDatetime
    mode: Mode
    demo: StrictBool
    vehicle: Vehicle
    summary: Summary
    coverage: Coverage
    sources: list[Source] = Field(max_length=100)
    facts: list[Fact] = Field(max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def honest_summary(self):
        if self.demo != (self.mode == "demo"):
            raise ValueError("Inconsistent data mode")
        if self.expires_at <= self.created_at:
            raise ValueError("Invalid report lifetime")
        if self.coverage.available > self.coverage.total:
            raise ValueError("Invalid coverage counts")
        # Never turn missing registries into a positive numeric assessment.
        key_blocks = {"registration", "restrictions", "pledges", "accidents", "mileage", "insurance", "debts"}
        has_key = any(b.id in key_blocks and b.status != "unavailable" for b in self.coverage.blocks)
        if self.mode == "live" and (self.summary.level == "unknown" or not has_key):
            self.summary.score = None
            self.summary.level = "unknown"
            self.summary.verdict = "Недостаточно данных"
            self.summary.headline = "Ключевые сведения неполны. Результат не подтверждает юридическую чистоту автомобиля"
        return self
