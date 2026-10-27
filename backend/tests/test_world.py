from datetime import timedelta
import httpx
import pytest
from betboot.providers.world import observations, competition_rows, discover, catalog, competitions
from betboot.providers.isports import agrees
from betboot.config import Settings
from betboot.research import ResearchProviders
from betboot.db import as_of_matches
from sqlalchemy.orm import Session


def profile(country='Bolivia',lid='13957',name='Bolivia Primera Division'):
    return {'leagueId':lid,'name':name,'country':country,'currentSeason':'2026'}


def row(now):
    return {'matchId':'1234','leagueId':'13957','leagueName':'Bolivia Primera Division','status':0,
            'matchTime':int((now+timedelta(hours=1)).timestamp()),'homeId':'1','awayId':'2',
            'homeName':'Synthetic home','awayName':'Synthetic away','neutral':False}


def test_global_identity_metadata_and_no_invented_result(now):
    profiles=competition_rows([profile()])
    matches,rejected=observations([row(now)],profiles,now)
    assert rejected==0 and len(matches)==1
    f=matches[0]
    assert f.league=='IS13957' and f.country=='Bolivia' and f.home_goals is None
    assert f.source=='isportsapi.com' and agrees(row(now),f)
    assert not agrees({**row(now),'homeId':'8'},f)
    assert not agrees({**row(now),'leagueId':'99'},f)
    assert not observations([{**row(now),'leagueName':'Wrong competition'}],profiles,now)[0]
    assert not observations([{**row(now),'status':-1}],profiles,now)[0]
    assert not observations([{**row(now),'extraExplain':{'extraTimeStatus':1}}],profiles,now)[0]


async def test_discovery_persists_and_caches_catalog(engine,now):
    calls=[]
    class Provider:
        async def fetch(self,endpoint,params=None):
            calls.append(endpoint)
            return ([profile()] if endpoint=='league' else [row(now)]),now.isoformat(),'https://api.isportsapi.com/sport/football/'+endpoint
    start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    await discover(engine,Provider(),start,start+timedelta(days=1))
    await discover(engine,Provider(),start,start+timedelta(days=1))
    assert calls.count('league')==1
    assert catalog(engine)['competitions']['13957']['country']=='Bolivia'
    assert any(c['country']=='Bolivia' for c in competitions(engine))
    with Session(engine) as db:
        matches=as_of_matches(db,now+timedelta(seconds=1))
    assert len(matches)==1 and matches[0].league_name=='Bolivia Primera Division'


async def test_unknown_odds_coverage_is_gap_not_exception(now):
    f=observations([row(now)],competition_rows([profile()]),now)[0][0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:pytest.fail('No odds call for unmapped league'))) as c:
        quotes,gap=await ResearchProviders(c,Settings(odds_api_key='test',_env_file=None)).odds(f)
    assert not quotes and 'not available' in gap


async def test_new_league_scan_keeps_fixture_but_does_not_spend_odds_quota(engine,now,monkeypatch):
    from betboot.collection import collect
    from betboot.providers.world import Batch
    from betboot.pipeline import scan
    f=observations([row(now)],competition_rows([profile()]),now)[0][0]
    async def discovery(*args):
        await collect(engine,Batch([f]),f.league,f.season)
        return [],0
    async def odds(*args):pytest.fail('No odds quota should be spent before baseline history exists')
    monkeypatch.setattr('betboot.pipeline.discover',discovery)
    monkeypatch.setattr(ResearchProviders,'odds',odds)
    result=await scan(engine,Settings(isports_api_key='test',_env_file=None),country='Bolivia',min_probability=.9)
    assert len(result['analyses'])==1
    analysis=result['analyses'][0]
    assert analysis['status']=='NO BET' and not analysis['recommendations']
    assert analysis['fixture']['country']=='Bolivia'
    assert 'api.isportsapi.com' in analysis['sources'][0]['url']


def test_core_competitions_are_not_duplicated_by_different_display_names(now):
    profiles=competition_rows([profile('Italy','1437','Italy Serie A')])
    event={**row(now),'leagueId':'1437','leagueName':'Italy Serie A'}
    assert observations([event],profiles,now)==([],0)


def test_country_and_global_scope_includes_lower_divisions(now):
    from betboot.domain import scope_matches
    from betboot.shortlist import in_scope
    from betboot.terminal import Request,parse_leagues
    f=observations([row(now)],competition_rows([profile()]),now)[0][0]
    payload=f.model_dump(mode='json')
    assert scope_matches(payload,parse_leagues('ALL'))
    assert scope_matches(payload,parse_leagues('Bolivia'))
    assert not scope_matches(payload,parse_leagues('England'))
    assert not scope_matches(payload,parse_leagues('Premier League'))
    request=Request(5,parse_leagues('Bolivia'),f.kickoff.date(),'UTC')
    assert in_scope(payload,request,now,now)
    assert not in_scope(payload,Request(5,parse_leagues('England'),f.kickoff.date(),'UTC'),now,now)


async def test_multi_country_discovery_filters_catalog(engine,now):
    class Provider:
        async def fetch(self,endpoint,params=None):
            profiles=[profile(),profile('Brazil','144','Brazil Serie A')]
            events=[row(now),{**row(now),'matchId':'999','leagueId':'144','leagueName':'Brazil Serie A'}]
            return (profiles if endpoint=='league' else events),now.isoformat(),'https://api.isportsapi.com/test'
    start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    await discover(engine,Provider(),start,start+timedelta(days=1),('bolivia',))
    with Session(engine) as db:rows=as_of_matches(db,now+timedelta(seconds=1))
    assert len(rows)==1 and rows[0].country=='Bolivia'


async def test_global_bootstrap_uses_league_season_and_preserves_provenance(engine,now):
    from betboot.providers.world import bootstrap_history
    calls=[]
    class Provider:
        async def fetch(self,endpoint,params=None):
            calls.append(params)
            if endpoint=='league':return [profile()],now.isoformat(),'https://api.isportsapi.com/league'
            if params and 'leagueId' in params:
                events=[{**row(now),'matchId':str(100+i),'status':-1,'homeScore':1,'awayScore':0,
                         'matchTime':int((now-timedelta(days=i+1)).timestamp())} for i in range(60)]
            else:events=[row(now)]
            return events,now.isoformat(),'https://api.isportsapi.com/schedule/basic'
    provider=Provider();start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    await discover(engine,provider,start,start+timedelta(days=1))
    runs,errors=await bootstrap_history(engine,provider,start,start+timedelta(days=2),('*',),3)
    assert runs and not errors and {'leagueId':'13957','season':'2026'} in calls
    with Session(engine) as db:rows=as_of_matches(db,now+timedelta(seconds=1))
    assert sum(m.status=='FINISHED' for m in rows)==60
    assert all(m.collected_at==now for m in rows)
    count=len(calls)
    await bootstrap_history(engine,provider,start,start+timedelta(days=2),('*',),3)
    assert len(calls)==count


def test_french_top_division_deduplicated(now):
    profiles=competition_rows([profile('France','1112','France Ligue 1')])
    assert observations([{**row(now),'leagueId':'1112','leagueName':'France Ligue 1'}],profiles,now)==([],0)


async def test_recent_short_season_import_still_fetches_prior_season(engine,now):
    from betboot.providers.world import bootstrap_history,Batch
    from betboot.collection import collect
    from betboot.store import save
    profiles=competition_rows([profile()]);f=observations([row(now)],profiles,now)[0][0]
    current=[f.model_copy(update={'external_id':str(500+i),'status':'FINISHED',
             'kickoff':now-timedelta(days=i+1),'home_goals':1,'away_goals':i%2,
             'away_id':'3'}) for i in range(60)]
    await collect(engine,Batch(current+[f]),f.league,f.season)
    save(engine,'world_catalog',{'competitions':profiles,'collected_at':now.isoformat(),'url':'https://api.isportsapi.com/league'})
    save(engine,'world_history_refresh',{'league':f.league,'collected_at':now.isoformat(),'rows':60})
    calls=[]
    class Provider:
        async def fetch(self,endpoint,params):
            calls.append((endpoint,params))
            if endpoint=='odds/european/all':return [],now.isoformat(),'https://api.isportsapi.com/odds'
            assert params=={'leagueId':'13957','season':'2025'}
            rows=[{**row(now),'matchId':str(800+i),'status':-1,'homeScore':1,'awayScore':i%2,
                   'matchTime':int((now-timedelta(days=365+i)).timestamp())} for i in range(60)]
            return rows,now.isoformat(),'https://api.isportsapi.com/schedule/basic'
    start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    runs,errors=await bootstrap_history(engine,Provider(),start,start+timedelta(days=2),('*',),1)
    assert runs and not errors
    assert ('schedule/basic',{'leagueId':'13957','season':'2025'}) in calls
    with Session(engine) as db:matches=as_of_matches(db,now+timedelta(seconds=1))
    assert sum(m.season==2025 for m in matches)==60


async def test_priced_league_history_precedes_cup_history(engine,now):
    from betboot.providers.world import bootstrap_history
    calls=[]
    regular={**row(now),'leagueType':1}
    cup={**row(now),'matchId':'4321','leagueId':'15027','leagueName':'Israel Cup','leagueType':2}
    class Provider:
        async def fetch(self,endpoint,params=None):
            if endpoint=='league':rows=[profile(),profile('Israel','15027','Israel Cup')]
            elif endpoint=='odds/european/all':
                rows=[{**e,'odds':[{'changeTime':now.timestamp(),'oddsDetail':'1,Test,2,3,4,2.1,3.4,3.2'}]} for e in (regular,cup)]
            elif 'leagueId' in params:
                calls.append(params['leagueId']);rows=[]
            else:rows=[regular,cup]
            return rows,now.isoformat(),'https://api.isportsapi.com/test'
    provider=Provider();start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    await discover(engine,provider,start,start+timedelta(days=2))
    await bootstrap_history(engine,provider,start,start+timedelta(days=2),('*',),1)
    assert calls==['13957','13957']
