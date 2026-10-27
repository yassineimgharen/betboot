from datetime import timedelta
import httpx
import pytest
from betboot.config import Settings
from betboot.research import ResearchProviders
from betboot.providers.isports_odds import parse_quotes


def sample(fixture,now):
    event={'matchId':'123','leagueId':'1639','leagueName':'English Premier League',
           'homeName':fixture.home_name,'awayName':fixture.away_name,'homeId':fixture.home_id,'awayId':fixture.away_id,
           'matchTime':int(fixture.kickoff.timestamp()),'status':0,'neutral':False}
    row={**event,'odds':[{'changeTime':now.timestamp(),'oddsDetail':'1,Test Book,8,9,10,2.1,3.4,3.2'}]}
    return event,row


def test_current_prices_only_freshness_and_identity(fixture,now):
    event,row=sample(fixture,now)
    quotes=parse_quotes([row],event,fixture,now.isoformat(),'https://api.isportsapi.com/sport/football/odds/european/all',600,now)
    assert [q['odds'] for q in quotes]==[2.1,3.4,3.2]
    assert {q['selection'] for q in quotes}=={'home','draw','away'}
    assert parse_quotes([{**row,'homeName':'Other'}],event,fixture,now.isoformat(),'url',600,now)==[]
    assert parse_quotes([row,row],event,fixture,now.isoformat(),'url',600,now)==[]
    row['odds'][0]['changeTime']=(now-timedelta(minutes=11)).timestamp()
    assert parse_quotes([row],event,fixture,now.isoformat(),'url',600,now)==[]


async def test_isports_fallback_survives_primary_quota_exhaustion(fixture,now,monkeypatch):
    f=fixture.model_copy(update={'source':'football-data.org'})
    event,row=sample(f,now);calls=[]
    def response(r):
        calls.append(r.url.path)
        return httpx.Response(401,json={'error_code':'OUT_OF_USAGE_CREDITS'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        provider=ResearchProviders(client,Settings(odds_api_key='synthetic',isports_api_key='synthetic',_env_file=None))
        async def fetch(endpoint,params=None):
            return ([event] if endpoint=='schedule/basic' else [{}] if endpoint=='odds/main' else [row]),now.isoformat(),'https://api.isportsapi.com/sport/football/'+endpoint
        monkeypatch.setattr(provider.isports,'fetch',fetch)
        for _ in range(2):
            quotes,gap=await provider.odds(f)
            assert len(quotes)==3 and gap is None
            assert all(q['source']=='iSports API' and 'api_key' not in q['url'] for q in quotes)
    assert len(calls)==1


def test_reviewed_psg_lyon_and_sevilla_aliases_require_canonical_ids(fixture,now):
    from betboot.providers.isports import agrees
    f=fixture.model_copy(update={'source':'football-data.org','league':'FL1','home_id':'524',
        'home_name':'Paris Saint-Germain FC','away_id':'523','away_name':'Olympique Lyonnais'})
    event={'leagueName':'France Ligue 1','homeName':'Paris Saint Germain (PSG)','awayName':'Lyon',
           'status':0,'matchTime':f.kickoff.timestamp()}
    assert agrees(event,f)
    assert not agrees(event,f.model_copy(update={'home_id':'999'}))
    assert not agrees({**event,'awayName':'Lyon Women'},f)
    f=f.model_copy(update={'league':'PD','home_id':'559','home_name':'Sevilla FC',
                          'away_id':'79','away_name':'CA Osasuna','away_short_name':'Osasuna'})
    assert agrees({**event,'leagueName':'Spanish La Liga','homeName':'Sevilla','awayName':'Osasuna'},f)


def test_totals_convert_hong_kong_and_reject_wrong_stage_or_line(fixture,now):
    from betboot.providers.isports_odds import parse_totals
    event,_=sample(fixture,now)
    fields=['123','50','2.5','0.8','0.9','2.5','0.85','0.95',str(now.timestamp()),'false','1']
    def parse(values):return parse_totals({'overUnder':[','.join(values)]},event,fixture,now.isoformat(),'https://api.isportsapi.com/sport/football/odds/main',600,now)
    result=parse(fields)
    assert [q['odds'] for q in result]==[1.85,1.95]
    assert [q['selection'] for q in result]==['over_2.5','under_2.5']
    assert all(q['bookmaker']=='1xbet' for q in result)
    for index,value in ((0,'other'),(5,'2.25'),(9,'true'),(10,'3'),(10,'0'),(8,str((now-timedelta(hours=1)).timestamp()))):
        bad=list(fields);bad[index]=value
        assert parse(bad)==[]
    assert parse_totals({'overUnderHalf':[','.join(fields)]},event,fixture,now.isoformat(),'url',600,now)==[]
