from typing import Protocol

from betboot.domain import League, MatchObservation


class ProviderError(Exception):
    """Sanitized error safe to report without revealing credentials or response bodies."""


class MatchProvider(Protocol):
    source: str

    async def matches(self, league: League, season: int) -> list[MatchObservation]: ...
