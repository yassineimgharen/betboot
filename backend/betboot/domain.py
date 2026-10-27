from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

LEAGUES = {
    "PL": "English Premier League",
    "PD": "Spanish La Liga",
    "SA": "Italian Serie A",
    "BL1": "German Bundesliga",
    "FL1": "Ligue 1",
}
League = Annotated[str, Field(pattern=r"^(PL|PD|SA|BL1|FL1|IS[0-9]{1,8})$")]
COUNTRIES = {"PL":"England", "PD":"Spain", "SA":"Italy", "BL1":"Germany", "FL1":"France"}


class MatchObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    league: League
    league_name: str | None = None
    country: str | None = None
    competition_type: Literal[1, 2] | None = None
    season: int = Field(ge=1900, le=2200)
    kickoff: AwareDatetime
    status: str
    home_id: str = Field(min_length=1)
    away_id: str = Field(min_length=1)
    home_name: str = Field(min_length=1)
    away_name: str = Field(min_length=1)
    home_short_name: str | None = None
    away_short_name: str | None = None
    home_goals: int | None = Field(default=None, ge=0, le=100)
    away_goals: int | None = Field(default=None, ge=0, le=100)
    # Last change according to the provider; distinct from our collection timestamp.
    source_updated_at: AwareDatetime | None = None
    collected_at: AwareDatetime

    @model_validator(mode="after")
    def validate_result(self):
        if self.home_id == self.away_id:
            raise ValueError("A team cannot play itself")
        if self.status == "FINISHED":
            if self.home_goals is None or self.away_goals is None:
                raise ValueError("Finished match requires both full-time scores")
            if self.kickoff >= self.collected_at:
                raise ValueError("Finished match cannot have a future kickoff")
        elif self.home_goals is not None or self.away_goals is not None:
            raise ValueError("Only finished regulation-time scores are accepted")
        if self.source_updated_at and self.source_updated_at > self.collected_at:
            raise ValueError("Provider timestamp is in the future")
        return self


def require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone is required")


def scope_matches(fixture, selectors):
    """Exact leagues or explicit country scopes; ALL is provider-catalog wide."""
    country=(fixture.get('country') or COUNTRIES.get(fixture['league']) or '').casefold()
    return ('*' in selectors or fixture['league'] in selectors or
            any(s.startswith('COUNTRY:') and s[8:].casefold()==country for s in selectors))
