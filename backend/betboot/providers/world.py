"""Provider-catalog driven discovery, with no assumed history or odds coverage."""
from datetime import UTC, datetime, timedelta
import re
from collections import defaultdict
from betboot.collection import collect
from betboot.domain import MatchObservation, LEAGUES, COUNTRIES
from betboot.providers.base import ProviderError
from betboot.store import read, save


CORE_WORLD_IDS={'1639','1134','1437','188','1112'}
CORE_WORLD_CODES={'IS'+lid for lid in CORE_WORLD_IDS}

def competition_rows(rows):
    result={}
    for row in rows:
        lid=str(row.get('leagueId',''))
        if not re.fullmatch(r'[0-9]{1,8}',lid) or not row.get('name') or not row.get('country'):
            continue
        result[lid]={'code':'IS'+lid,'name':row['name'],'country':row['country'],
                     'season':row.get('currentSeason',''),'source':'iSports API'}
    return result


def catalog(engine):
    records=read(engine,'world_catalog',1)
    return records[0] if records else None


def competitions(engine):
    rows=[{'code':code,'name':name,'country':COUNTRIES[code],'source':'football-data.org'} for code,name in LEAGUES.items()]
    saved=catalog(engine)
    if saved:
        rows.extend(row for row in saved['competitions'].values() if row['code'] not in CORE_WORLD_CODES)
    return rows


async def load_catalog(engine,provider):
    saved=catalog(engine)
    if saved and 0 <= (datetime.now(UTC)-datetime.fromisoformat(saved['collected_at'])).total_seconds()<86400:
        return saved
    rows,stamp,url=await provider.fetch('league')
    values=competition_rows(rows)
    if not values:
        raise ProviderError('iSports league catalog contained no usable country/league identities')
    payload={'competitions':values,'collected_at':stamp,'url':url}
    save(engine,'world_catalog',payload)
    return payload


def observations(rows,profiles,stamp):
    accepted=[]; rejected=0; seen=set()
    for row in rows:
        try:
            lid=str(row['leagueId']); profile=profiles.get(lid)
            if not profile or lid in CORE_WORLD_IDS:
                continue  # Keep core competitions on their established identity/history source.
            if row['leagueName']!=profile['name'] or row.get('status') not in (0,-1):
                continue
            if row.get('neutral') or row.get('extraExplain',{}).get('extraTimeStatus',0):
                continue
            season=re.fullmatch(r'(\d{4})(?:-\d{4})?',str(profile['season']))
            if not season:
                rejected+=1; continue
            for field in ('matchId','homeId','awayId'):
                if not re.fullmatch(r'[1-9][0-9]*',str(row[field])):
                    raise ValueError('Invalid identity')
            if str(row['matchId']) in seen:
                raise ValueError('Duplicate identity')
            finished=row['status']==-1
            match=MatchObservation(source='isportsapi.com',external_id=str(row['matchId']),league=profile['code'],
                league_name=profile['name'],country=profile['country'],competition_type=row.get('leagueType'),season=int(season[1]),
                kickoff=datetime.fromtimestamp(row['matchTime'],UTC),status='FINISHED' if finished else 'TIMED',
                home_id=str(row['homeId']),away_id=str(row['awayId']),home_name=row['homeName'],away_name=row['awayName'],
                home_goals=row['homeScore'] if finished else None,away_goals=row['awayScore'] if finished else None,
                collected_at=stamp)
            accepted.append(match);seen.add(match.external_id)
        except (ValueError,KeyError,TypeError,OverflowError):
            rejected+=1
    return accepted,rejected


class Batch:
    source='isportsapi.com'
    def __init__(self,rows):self.rows=rows
    async def matches(self,league,season):return self.rows


async def discover(engine,provider,start,end,country=None):
    saved=await load_catalog(engine,provider)
    profiles=saved['competitions']
    if country:
        countries={c.casefold() for c in ((country,) if isinstance(country,str) else country)}
        known={v['country'].casefold() for v in profiles.values()}
        if not countries<=known:raise ProviderError('Unknown country in provider catalog: '+', '.join(sorted(countries-known)))
        profiles={k:v for k,v in profiles.items() if v['country'].casefold() in countries}
    day=start.astimezone(UTC).date(); last=(end-timedelta(microseconds=1)).astimezone(UTC).date()
    runs=[]; rejected=0
    while day<=last:
        rows,stamp,url=await provider.fetch('schedule/basic',{'date':day.isoformat()})
        matches,bad=observations(rows,profiles,stamp);rejected+=bad
        grouped=defaultdict(list)
        for match in matches:grouped[(match.league,match.season)].append(match)
        for (league,season),batch in grouped.items():
            runs.append(await collect(engine,Batch(batch),league,season))
        day+=timedelta(days=1)
    return runs,rejected


async def bootstrap_history(engine,provider,start,end,selectors,limit=3):
    """Fetch a bounded number of current-season result batches using documented leagueId."""
    from sqlalchemy.orm import Session
    from betboot.db import as_of_matches
    from betboot.domain import scope_matches
    saved=await load_catalog(engine,provider)
    now=datetime.now(UTC)
    with Session(engine) as db:known=as_of_matches(db,now)
    upcoming=[m for m in known if m.source=='isportsapi.com' and m.status=='TIMED' and
              m.kickoff>now and start<=m.kickoff<end and scope_matches(m.model_dump(),selectors)]
    groups=defaultdict(list)
    for m in upcoming:groups[m.league].append(m)
    def sufficient(fixtures):
        results=[m for m in known if m.source=='isportsapi.com' and m.league==fixtures[0].league and
                 m.status=='FINISHED' and now-timedelta(days=730)<=m.kickoff<now]
        return len(results)>=50 and all(
            sum(m.home_id==f.home_id for m in results)>=5 and
            sum(m.away_id==f.away_id for m in results)>=5 for f in fixtures)
    recent=read(engine,'world_history_refresh',1000)
    refreshed={r['league'] for r in recent if r.get('prior_season_checked') and
               0<=(now-datetime.fromisoformat(r['collected_at'])).total_seconds()<86400}
    groups={league:fixtures for league,fixtures in groups.items() if league not in refreshed and not sufficient(fixtures)}
    errors=[];runs=[];attempts=0
    # Prefer usable priced fixtures. A league with many unpriced games should not consume the budget first.
    priced=set()
    if groups and limit:
        try:
            from betboot.providers.isports_odds import parse_quotes
            prices,collected,url=await provider.fetch('odds/european/all',{'date':start.astimezone(UTC).date().isoformat()})
            for fixtures in groups.values():
                for f in fixtures:
                    event={'matchId':f.external_id,'leagueName':f.league_name,'homeName':f.home_name,
                           'awayName':f.away_name,'matchTime':int(f.kickoff.timestamp())}
                    if any(q['odds']>=1.5 for q in parse_quotes(prices,event,f,collected,url,600,datetime.now(UTC))):
                        priced.add(f.external_id)
        except ProviderError as exc:errors.append(f'World history odds prioritization: {exc}')
    for league,fixtures in sorted(groups.items(),key=lambda item:(not any(m.external_id in priced for m in item[1]),item[1][0].competition_type==2,-sum(m.external_id in priced for m in item[1]),-len(item[1]),item[0])):
        if attempts>=limit:break
        attempts+=1;lid=league[2:];profile=saved['competitions'].get(lid)
        if not profile:continue
        parts=str(profile['season']).split('-')
        seasons=[profile['season'],'-'.join(str(int(part)-1) for part in parts)]
        imported=0;prior_checked=False
        for index,season in enumerate(seasons):
            if sufficient(fixtures):break
            target={**profile,'season':season}
            # Reuse current-season downloads; older runs may still need the previous season.
            already=[m for m in known if m.source=='isportsapi.com' and m.league==league and
                     m.season==int(str(season).split('-')[0]) and m.status=='FINISHED']
            if index==0 and len(already)>=50:continue
            try:
                rows,stamp,_=await provider.fetch('schedule/basic',{'leagueId':lid,'season':season})
                matches,rejected=observations(rows,{lid:target},stamp)
                existing={m.external_id:m for m in known if m.source=='isportsapi.com'}
                matches=[m for m in matches if m.external_id not in existing or existing[m.external_id].season==m.season]
                if matches:
                    runs.append(await collect(engine,Batch(matches),league,matches[0].season));imported+=len(matches)
                    updated={(m.source,m.external_id):m for m in known}
                    updated.update({(m.source,m.external_id):m for m in matches});known=list(updated.values())
                if rejected:errors.append(f'{league}: rejected {rejected} historical records')
            except ProviderError as exc:errors.append(f'{league} history: {exc}')
            if index==1:prior_checked=True
        save(engine,'world_history_refresh',{'league':league,'collected_at':datetime.now(UTC).isoformat(),
             'rows':imported,'prior_season_checked':prior_checked,'history_sufficient':sufficient(fixtures)})
    return runs,errors
