"""football-data.org v4: fixtures, statuses and historical results for the core leagues."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from slipbot.http import SourceError

BASE = 'https://api.football-data.org/v4/'
SPACING = 6.5           # free tier: 10 requests per minute
NOT_STARTED = {'SCHEDULED', 'TIMED'}


@dataclass(frozen=True)
class Match:
    """A fixture or result. Fixtures found only through iSports have id 'is:<matchId>' and
    team ids equal to their normalised names."""
    id: int | str
    league: str
    kickoff: datetime
    status: str
    home_id: int
    away_id: int
    home: str
    away: str
    home_short: str
    away_short: str
    home_goals: int | None = None
    away_goals: int | None = None
    isports_id: str | None = None      # matchId in iSports odds feeds, once identified


def _parse(row, league):
    score = (row.get('score') or {}).get('fullTime') or {}
    home, away = row['homeTeam'], row['awayTeam']
    if not home.get('id') or not away.get('id'):
        return None   # knockout placeholders without teams
    return Match(
        id=row['id'], league=league, status=row['status'],
        kickoff=datetime.fromisoformat(row['utcDate'].replace('Z', '+00:00')),
        home_id=home['id'], away_id=away['id'], home=home.get('name') or '', away=away.get('name') or '',
        home_short=home.get('shortName') or home.get('name') or '',
        away_short=away.get('shortName') or away.get('name') or '',
        home_goals=score.get('home'), away_goals=score.get('away'))


def _matches(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('matches'), list):
        message = payload.get('message') if isinstance(payload, dict) else None
        raise SourceError(f'football-data.org: {message or "unexpected response"}')
    return payload


def season_of(day):
    """European seasons start in July; football-data.org names them by start year."""
    return day.year if day.month >= 7 else day.year - 1


class FootballData:
    def __init__(self, fetcher, token):
        if not token:
            raise SourceError('FOOTBALL_DATA_TOKEN is missing from .env')
        self.fetcher = fetcher
        self.headers = {'X-Auth-Token': token}

    def _get(self, path, params, cache_key, max_age, stale_ok=False):
        return self.fetcher.get_cached(BASE + path, params=params, headers=self.headers,
                                       slot='football-data.org', spacing=SPACING, stale_ok=stale_ok,
                                       cache_key=cache_key, max_age=max_age, validate=_matches)

    def season(self, league, season, now):
        """All matches of one league season. Finished seasons are cached permanently."""
        finished = season < season_of(now.date())
        payload = self._get(f'competitions/{league}/matches', {'season': season},
                            f'fd:{league}:{season}', None if finished else 12 * 3600, stale_ok=True)
        return [m for row in payload['matches'] if (m := _parse(row, league))]

    def history(self, league, now, seasons=3):
        """Finished results from the current and previous seasons, oldest first."""
        current = season_of(now.date())
        results = []
        for season in range(current - seasons + 1, current + 1):
            results += [m for m in self.season(league, season, now)
                        if m.status == 'FINISHED' and m.home_goals is not None]
        return sorted(results, key=lambda m: m.kickoff)

    def fixtures(self, leagues, start, end):
        """Fresh (10-minute) list of not-yet-started fixtures kicking off in [start, end)."""
        # The API filters by UTC date; the caller's window may be in another timezone.
        params = {'competitions': ','.join(leagues),
                  'dateFrom': start.astimezone(UTC).date().isoformat(),
                  'dateTo': (end.astimezone(UTC) + timedelta(days=1)).date().isoformat()}
        key = 'fd:fixtures:' + ':'.join(f'{k}={v}' for k, v in sorted(params.items()))
        payload = self._get('matches', params, key, 600)
        found = []
        for row in payload['matches']:
            league = (row.get('competition') or {}).get('code')
            match = _parse(row, league) if league in leagues else None
            if match and match.status in NOT_STARTED and start <= match.kickoff < end:
                found.append(match)
        return sorted(found, key=lambda m: m.kickoff)


def utcnow():
    return datetime.now(UTC)
