"""Authorized API research with explicit provenance and conservative identity matching."""
import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from betboot.domain import LEAGUES
from betboot.providers.base import ProviderError
from betboot.identity import team_matches

SPORTS={'PL':'soccer_epl','PD':'soccer_spain_la_liga','SA':'soccer_italy_serie_a','BL1':'soccer_germany_bundesliga','FL1':'soccer_france_ligue_one'}
API_LEAGUES={'PL':39,'PD':140,'SA':135,'BL1':78,'FL1':61}
# Explicit mappings verified against both provider catalogs. No fuzzy league joins.
WORLD_SPORTS = {'IS144': ('Brazil Serie A', 'soccer_brazil_campeonato'), 'IS1112': ('France Ligue 1', 'soccer_france_ligue_one'), 'IS1213': ('France Ligue 2', 'soccer_france_ligue_two'), 'IS1011': ('Russia Premier League', 'soccer_russia_premier_league'), 'IS1325': ('Liga Portugal 1', 'soccer_portugal_primeira_liga'), 'IS1617': ('Holland Eredivisie', 'soccer_netherlands_eredivisie'), 'IS155': ('Belgian Pro League', 'soccer_belgium_first_div'), 'IS133': ('Austrian Bundesliga', 'soccer_austria_bundesliga'), 'IS177': ('Denmark Superliga', 'soccer_denmark_superliga'), 'IS1314': ('Finland Veikkausliga', 'soccer_finland_veikkausliiga'), 'IS1224': ('Norway Eliteserien', 'soccer_norway_eliteserien'), 'IS1628': ('Swedish Allsvenskan', 'soccer_sweden_allsvenskan'), 'IS1729': ('Switzerland Super League', 'soccer_switzerland_superleague'), 'IS166': ('Poland Ekstraklasa', 'soccer_poland_ekstraklasa'), 'IS1235': ('Greece Super League A', 'soccer_greece_super_league'), 'IS1033': ('Turkey Super Lig', 'soccer_turkey_super_league'), 'IS1921': ('Scottish Premiership', 'soccer_spl'), 'IS111': ('Ireland Premier Division', 'soccer_league_of_ireland'), 'IS1527': ('Japan J1 League', 'soccer_japan_j_league'), 'IS10415': ('Primera Division Liga MX', 'soccer_mexico_ligamx'), 'IS122': ('Argentine Division 1', 'soccer_argentina_primera_division'), 'IS15140': ('Chile Primera Division', 'soccer_chile_campeonato'), 'IS13722': ('Australia A-League', 'soccer_australia_aleague'), 'IS12923': ('Saudi Professional League', 'soccer_saudi_arabia_pro_league')}

# Prevent HTTP libraries logging an odds API query-string credential.
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)


def same_name(a,b):
    return ' '.join(a.casefold().split()) == ' '.join(b.casefold().split())


def matched(event, fixture, home='home_team', away='away_team', time='commence_time', provider=None):
    try:
        kickoff=datetime.fromisoformat(event[time].replace('Z','+00:00'))
        return team_matches(event[home],fixture,'home',provider) and team_matches(event[away],fixture,'away',provider) and abs((kickoff-fixture.kickoff).total_seconds())<=60
    except (KeyError,ValueError,TypeError):
        return False


async def get_json(client,url,params=None,headers=None):
    for attempt in range(3):
        if url.startswith('https://v3.football.api-sports.io/'):
            # Every request, including retries and the next match's first request,
            # observes conservative account spacing. 429 still terminates the call.
            await asyncio.sleep(6.1)
        try:
            response=await client.get(url,params=params,headers=headers,timeout=25)
        except httpx.TransportError:
            if attempt==2:
                raise ProviderError('Research provider transport unavailable') from None
            await asyncio.sleep(2**attempt); continue
        if response.status_code==429:
            # Respect embargo by stopping. A later scheduled scan can retry.
            raise ProviderError('Research provider quota exhausted; retry on a later scan')
        if response.status_code>=500 and attempt<2:
            await asyncio.sleep(2**attempt); continue
        if response.status_code!=200 and url.startswith('https://api.the-odds-api.com/'):
            try:
                payload=response.json()
                code=payload.get('error_code') if isinstance(payload,dict) else None
            except ValueError:code=None
            if code=='OUT_OF_USAGE_CREDITS':
                raise ProviderError('The Odds API quota exhausted: no usage credits remain; restore the plan allowance or wait for its reset')
            if code in ('INVALID_API_KEY','MISSING_API_KEY'):
                raise ProviderError('The Odds API authentication failed; check ODDS_API_KEY in .env')
        if response.status_code!=200:
            raise ProviderError(f'Research provider returned HTTP {response.status_code}')
        try:
            return response.json()
        except ValueError:
            raise ProviderError('Research provider returned invalid JSON') from None
    raise ProviderError('Research retries exhausted')


class ResearchProviders:
    def __init__(self,client,settings):
        self.client,self.settings=client,settings
        self.odds_cache={}
        self.odds_failure=None
        self.extra_odds_cache={}
        self.extra_odds_paused=False
        from betboot.providers.isports import ISportsContext
        self.isports=ISportsContext(client,settings)

    async def odds(self,fixture):
        issue=None
        try:
            quotes,issue=await self._primary_odds(fixture)
            if quotes:return quotes,issue
        except ProviderError as exc:
            if not self.settings.isports_api_key.get_secret_value():raise
            issue=str(exc)
        if not self.settings.isports_api_key.get_secret_value():return [],issue
        from betboot.providers.isports_odds import odds
        try:
            quotes,gap=await odds(self.isports,fixture,self.settings.odds_max_age_seconds)
            if quotes:return quotes,None
            return [],'; '.join(g for g in (issue,gap) if g)
        except ProviderError as exc:
            return [],'; '.join(g for g in (issue,str(exc)) if g)

    async def _primary_odds(self,fixture):
        if self.odds_failure:raise ProviderError(self.odds_failure)
        key=self.settings.odds_api_key.get_secret_value()
        if not key:
            return [],'The Odds API key is not configured'
        sport=SPORTS.get(fixture.league)
        mapping=WORLD_SPORTS.get(fixture.league)
        if fixture.source=='isportsapi.com' and mapping and mapping[0]==fixture.league_name:
            sport=mapping[1]
        if not sport:
            return [], 'Verified odds integration is not available for this competition'
        url=f'https://api.the-odds-api.com/v4/sports/{sport}/odds'
        if sport not in self.odds_cache:
            try:
                self.odds_cache[sport]=await get_json(self.client,url,{'apiKey':key,'regions':'uk,eu',
                                                   'markets':'h2h,totals,spreads','oddsFormat':'decimal'})
            except ProviderError as exc:
                if 'quota exhausted' in str(exc) or 'authentication failed' in str(exc):self.odds_failure=str(exc)
                raise
        events=self.odds_cache[sport]
        if not isinstance(events,list):
            raise ProviderError('Invalid odds response shape')
        candidates=[e for e in events if matched(e,fixture,provider='the-odds-api')]
        if len(candidates)!=1:
            return [],'No unique exact/explicit-alias team and kickoff match in odds feed; no fuzzy join applied'
        event=candidates[0]; now=datetime.now(UTC); quotes=[]
        extra_books=[]
        if self.settings.odds_extra_markets and not self.extra_odds_paused:
            event_url=f"https://api.the-odds-api.com/v4/sports/{sport}/events/{event['id']}/odds"
            if event['id'] not in self.extra_odds_cache:
                try:
                    extra=await get_json(self.client,event_url,{'apiKey':key,'regions':'uk,eu',
                        'markets':'alternate_totals,btts,double_chance','oddsFormat':'decimal'})
                    self.extra_odds_cache[event['id']]=extra
                except ProviderError as exc:
                    self.extra_odds_paused='quota exhausted' in str(exc)
                    self.extra_odds_cache[event['id']]=None
            extra=self.extra_odds_cache[event['id']]
            if isinstance(extra,dict) and extra.get('id')==event['id'] and matched(extra,fixture,provider='the-odds-api'):
                extra_books=extra.get('bookmakers',[])
        
        event_home,event_away=event['home_team'],event['away_team']
        for book in event.get('bookmakers',[])+extra_books:
            for market in book.get('markets',[]):
                stamp=market.get('last_update',book.get('last_update'))
                try:
                    updated=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                    age=(now-updated).total_seconds()
                    if age<0 or age>self.settings.odds_max_age_seconds or fixture.kickoff<=now:
                        continue
                    outcomes=market['outcomes']
                    prices=[float(o['price']) for o in outcomes]
                    if any(not math.isfinite(p) or p<=1 for p in prices):
                        continue
                    complete=market['key']=='h2h' and len(outcomes)==3 and {o['name'] for o in outcomes}=={event_home,event_away,'Draw'}
                    overround=sum(1/p for p in prices) if complete else None
                    if complete and not .95<=overround<=1.3:
                        continue
                    for o in outcomes:
                        selection=None
                        if market['key']=='h2h' and complete:
                            selection='home' if o['name']==event_home else 'away' if o['name']==event_away else 'draw'
                        elif market['key'] in ('totals','alternate_totals') and o.get('point') in (1.5,2.5,3.5) and o['name'] in ('Over','Under'):
                            selection=f"{o['name'].lower()}_{o['point']}"
                        elif market['key']=='btts' and o['name'] in ('Yes','No'):
                            selection='btts_'+o['name'].lower()
                        elif market['key']=='double_chance':
                            # Accept explicit labels only. Never infer double-chance odds from 1X2 prices.
                            labels={'1X':'1X','X2':'X2','12':'12',
                                    f'{event_home} or Draw':'1X',f'Draw or {event_away}':'X2',
                                    f'{event_home} or {event_away}':'12',
                                    'Home or Draw':'1X','Draw or Away':'X2','Home or Away':'12'}
                            selection=labels.get(o['name'])
                        elif market['key']=='spreads' and o['name'] in (event_home,event_away):
                            line=float(o['point'])
                            if not math.isfinite(line) or abs(line)>10 or line*4!=round(line*4):
                                continue
                            selection=f"ah_{'home' if o['name']==event_home else 'away'}_{line}"
                        if selection:
                            quotes.append({'selection':selection,'odds':float(o['price']),'bookmaker':book['title'],
                                'bookmaker_key':book['key'],'updated_at':updated.isoformat(),'collected_at':now.isoformat(),
                                'source':'The Odds API','url':(event_url if book in extra_books else url),'event_id':event['id'],
                                'margin_removed_probability':(1/float(o['price']))/overround if complete else None})
                except (KeyError,TypeError,ValueError):
                    continue
        return quotes,None if quotes else 'No supported fresh pre-match quotes returned'

    async def context(self,fixture,deep=False):
        if fixture.source == 'isportsapi.com':
            return await self.isports.context(fixture,deep)
        key=self.settings.api_football_key.get_secret_value()
        if not key:
            if self.settings.isports_api_key.get_secret_value():
                return await self.isports.context(fixture,deep)
            return [],False,['No context provider configured: set API_FOOTBALL_KEY or ISPORTS_API_KEY']
        base='https://v3.football.api-sports.io'
        params={'league':API_LEAGUES[fixture.league],'season':fixture.season,'date':fixture.kickoff.date().isoformat()}
        data=await get_json(self.client,base+'/fixtures',params,{'x-apisports-key':key})
        if data.get('errors'):
            raise ProviderError('API-Football account could not access requested fixtures')
        candidates=[]
        for e in data.get('response',[]):
            normalized={'home_team':e['teams']['home']['name'],'away_team':e['teams']['away']['name'], 'commence_time':e['fixture']['date']}
            if matched(normalized,fixture):
                candidates.append(e)
        if len(candidates)!=1:
            return [],False,['Independent fixture identity could not be cross-checked exactly']
        event=candidates[0]; now=datetime.now(UTC).isoformat(); fid=event['fixture']['id']
        evidence=[{'kind':'fixture','source':'API-Football','url':base+'/fixtures?'+urlencode(params),
                   'collected_at':now,'verification':'Independent fixture/kickoff agreement','data':event}]
        missing=[]
        for endpoint in ('injuries','fixtures/lineups'):
            data=await get_json(self.client,base+'/'+endpoint,{'fixture':fid},{'x-apisports-key':key})
            if data.get('errors') or not data.get('response'):
                missing.append(f'{endpoint}: no entitled or confirmed records; empty response does not prove absence')
                continue
            evidence.append({'kind':'injuries' if endpoint=='injuries' else 'lineups','source':'API-Football',
                'url':f'{base}/{endpoint}?fixture={fid}','collected_at':datetime.now(UTC).isoformat(),
                'verification':'Single provider report; not independently corroborated','data':data['response']})
        if deep:
            data=await get_json(self.client,base+'/standings',{'league':API_LEAGUES[fixture.league],'season':fixture.season},{'x-apisports-key':key})
            if data.get('response'):
                evidence.append({'kind':'standings','source':'API-Football','url':base+'/standings?'+urlencode({'league':API_LEAGUES[fixture.league],'season':fixture.season}),
                    'collected_at':datetime.now(UTC).isoformat(),'verification':'Single provider report','data':data['response']})
        return evidence,True,missing


async def official_feeds(client,configured_urls,fixture):
    """Optional publisher-permitted RSS/Atom feeds; headlines are context, never model facts.

    URLs are configured by the server owner after verifying reuse permission. Robots
    disallow or inaccessible robots policy fails closed. No search-result scraping.
    """
    from urllib.parse import urlparse
    from urllib.robotparser import RobotFileParser
    from xml.etree import ElementTree as ET
    evidence=[]; gaps=[]
    for url in configured_urls[:8]:
        parsed=urlparse(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):
            gaps.append('Official feed URL rejected: HTTPS without credentials is required');continue
        if parsed.hostname in ('localhost',) or '.' not in parsed.hostname:
            gaps.append('Official feed host rejected');continue
        try:
            robots_url=f'https://{parsed.netloc}/robots.txt'
            robots=await client.get(robots_url,timeout=15,headers={'User-Agent':'BetbootResearch/0.2'})
            if robots.status_code!=200:
                gaps.append(f'{parsed.hostname}: robots policy unavailable; skipped');continue
            policy=RobotFileParser();policy.parse(robots.text.splitlines())
            if not policy.can_fetch('BetbootResearch',url):
                gaps.append(f'{parsed.hostname}: automated access disallowed');continue
            delay=policy.crawl_delay('BetbootResearch') or policy.crawl_delay('*') or 2
            if delay>60:
                gaps.append(f'{parsed.hostname}: crawl delay exceeds job budget');continue
            await asyncio.sleep(max(2,delay))
            async with client.stream('GET',url,timeout=20,headers={'User-Agent':'BetbootResearch/0.2'}) as response:
                if response.status_code!=200:
                    gaps.append(f'{parsed.hostname}: feed unavailable');continue
                content=b''
                async for chunk in response.aiter_bytes():
                    content+=chunk
                    if len(content)>1_000_000:
                        raise ValueError('Feed exceeds size limit')
            root=ET.fromstring(content)
            now=datetime.now(UTC).isoformat()
            for item in list(root.findall('.//item'))+list(root.findall('.//{http://www.w3.org/2005/Atom}entry')):
                title=item.findtext('title') or item.findtext('{http://www.w3.org/2005/Atom}title') or ''
                names=[fixture.home_name.casefold().removesuffix(' fc'),fixture.away_name.casefold().removesuffix(' fc')]
                if not any(name in title.casefold() for name in names):continue
                link=item.findtext('link')
                if not link:
                    element=item.find('{http://www.w3.org/2005/Atom}link');link=element.get('href') if element is not None else None
                if not link or urlparse(link).scheme!='https' or urlparse(link).hostname!=parsed.hostname:continue
                evidence.append({'kind':'official_feed_headline','source':parsed.hostname,'url':link,'collected_at':now,
                    'verification':'Publisher headline only; not extracted statistical evidence',
                    'data':{'title':title,'feed_url':url,'published':item.findtext('pubDate') or item.findtext('{http://www.w3.org/2005/Atom}published')}})
        except (httpx.HTTPError,ET.ParseError,ValueError):
            gaps.append(f'{parsed.hostname}: feed could not be validated')
    return evidence,gaps
