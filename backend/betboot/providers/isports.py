"""iSports Live Data context. HTTPS only; credentials never enter evidence.

Batch requests are shared within a scan. Endpoint calls are spaced by 90 seconds
within this process, including failed requests. Run one research worker per key.
"""
import asyncio
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from betboot.identity import team_matches
from betboot.providers.base import ProviderError

BASE = 'https://api.isportsapi.com/sport/football/'
_last_request = {}
_request_lock = asyncio.Lock()
LEAGUE_NAMES = {'PL': 'English Premier League', 'PD': 'Spanish La Liga',
                'SA': 'Italy Serie A', 'BL1': 'German Bundesliga', 'FL1': 'France Ligue 1'}


def team_agrees(name, fixture, side):
    if not isinstance(name, str):
        return False
    return team_matches(name,fixture,side,'isports')


def agrees(event, fixture):
    try:
        return ((event['leagueName'] == fixture.league_name and 'IS'+str(event['leagueId']) == fixture.league
                 and str(event['homeId']) == fixture.home_id and str(event['awayId']) == fixture.away_id
                 if fixture.source == 'isportsapi.com' else event['leagueName'] == LEAGUE_NAMES[fixture.league])
                and event['status'] == 0 and not event.get('neutral', False)
                and abs(float(event['matchTime']) - fixture.kickoff.timestamp()) <= 60
                and team_agrees(event['homeName'], fixture, 'home')
                and team_agrees(event['awayName'], fixture, 'away'))
    except (KeyError, TypeError, ValueError):
        return False


class ISportsContext:
    def __init__(self, client, settings):
        self.client = client
        self.key = settings.isports_api_key.get_secret_value()
        self.cache = {}
        self.errors = {}

    async def fetch(self, endpoint, params=None):
        params = params or {}
        cache_key = (endpoint, tuple(sorted(params.items())))
        if cache_key in self.cache:
            return self.cache[cache_key]
        if endpoint in self.errors:
            raise ProviderError(self.errors[endpoint])
        try:
            # Global spacing prevents consecutive scans from exceeding endpoint limits.
            async with _request_lock:
                while (remaining := (1800 if endpoint == 'league' else 90) - (time.monotonic() - _last_request.get(endpoint, -1e9))) > 0:
                    await asyncio.sleep(min(remaining, 45))
                _last_request[endpoint] = time.monotonic()
                response = await self.client.get(BASE + endpoint, params={**params, 'api_key': self.key}, timeout=25)
            if response.status_code == 429:
                raise ProviderError('iSports quota exhausted; retry on a later scan')
            if response.status_code != 200:
                raise ProviderError(f'iSports returned HTTP {response.status_code}')
            data = response.json()
            if not isinstance(data, dict) or data.get('code') != 0:
                raise ProviderError('iSports denied this endpoint; check key and Live Data entitlement')
            rows = data.get('data')
            if rows is None:
                rows = []
            if endpoint=='odds/main' and isinstance(rows,dict):
                rows=[rows]  # This documented endpoint returns named market arrays.
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ProviderError('iSports returned an unsupported response structure')
            result = (rows, datetime.now(UTC).isoformat(), BASE + endpoint + ('?' + urlencode(params) if params else ''))
            self.cache[cache_key] = result
            return result
        except (httpx.HTTPError, ValueError):
            message = 'iSports request failed; transport or JSON response unavailable'
            self.errors[endpoint] = message
            raise ProviderError(message) from None
        except ProviderError as exc:
            self.errors[endpoint] = str(exc)
            raise

    async def context(self, fixture, deep=False):
        rows, stamp, url = await self.fetch('schedule/basic', {'date': fixture.kickoff.astimezone(UTC).date().isoformat()})
        candidates = [row for row in rows if agrees(row, fixture)]
        if len(candidates) != 1:
            return [], False, ['iSports: no unique pre-match team, league and kickoff agreement']
        event = candidates[0]
        mid = str(event['matchId'])
        evidence = [{'kind': 'fixture', 'source': 'iSports API', 'url': url, 'collected_at': stamp,
                     'verification': 'Provider fixture/league/kickoff agreement' if fixture.source == 'isportsapi.com' else 'Independent pre-match fixture/league/kickoff agreement', 'data': event}]
        missing = []
        for endpoint, kind in [('injury', 'injuries'), ('lineups', 'lineups')]:
            params = {'isPreview': 'true'} if endpoint == 'lineups' else {}
            # Deep analysis can access a specific match within the provider's ±3-day window.
            if endpoint == 'lineups' and deep:
                params['matchId'] = mid
            try:
                records, collected, source = await self.fetch(endpoint, params)
                records = [row for row in records if str(row.get('matchId')) == mid]
                if kind == 'injuries':
                    teams = {str(event['homeId']), str(event['awayId'])}
                    records = [row for row in records if str(row.get('teamId')) in teams and row.get('name') and row.get('reason')]
                else:
                    records = [row for row in records if all(isinstance(row.get(side), list) and len(row[side]) == 11
                        and all(isinstance(p, dict) and p.get('playerId') and p.get('name') for p in row[side])
                        and len({str(p['playerId']) for p in row[side]}) == 11 for side in ('homeLineup', 'awayLineup'))]
                if not records:
                    missing.append(f'iSports {kind}: no usable match records; empty data does not prove absence'
                                   + ('; Deep Analyze Match checks the extended preview window' if kind == 'lineups' and not deep else ''))
                    continue
                evidence.append({'kind': kind, 'source': 'iSports API', 'url': source, 'collected_at': collected,
                    'verification': ('Provider lineup preview; predicted or official, confirmation not verified' if kind == 'lineups'
                                     else 'Single provider injury/suspension reports; not independently corroborated'), 'data': records})
            except ProviderError as exc:
                missing.append(str(exc))
        independent = fixture.source != 'isportsapi.com'
        if not independent:
            missing.append('iSports context shares the fixture source; independent fixture confirmation requires another provider')
        return evidence, independent, missing
