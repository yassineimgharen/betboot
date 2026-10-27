from datetime import UTC,date,datetime,timedelta
from io import StringIO
from contextlib import redirect_stdout
import pytest
import httpx
from betboot.terminal import Request,ask,parse_leagues,select_picks,render,generate
from betboot.config import Settings
from betboot.research import ResearchProviders
from betboot.providers.isports import agrees


def test_exactly_three_prompts_and_local_dates():
    prompts=[];answers=iter(['3','Premier League, Ligue 1','Tomorrow'])
    def respond(prompt):prompts.append(prompt);return next(answers)
    # UTC Oct 25 is still Oct 24 in New York.
    req=ask('America/New_York',respond,datetime(2026,10,25,1,tzinfo=UTC))
    assert len(prompts)==3 and req.day==date(2026,10,25) and req.leagues==('PL','FL1')
    assert parse_leagues('ALL')==('*',)
    assert len(parse_leagues('TOP5'))==5
    assert parse_leagues('Brazil')==('COUNTRY:brazil',)
    with pytest.raises(ValueError):parse_leagues('!!!')


def pick(now,league='PL',event='a'):
    return {'fixture':{'source':'football-data.org','external_id':event,'league':league,'home_id':'1','away_id':'2',
        'home_name':'Test Home','away_name':'Test Away','kickoff':(now+timedelta(hours=1)).isoformat(),
        'collected_at':now.isoformat(),'status':'TIMED'},'selection':'home','odds':1.5,'bookmaker':'Synthetic book',
        'updated_at':now.isoformat(),'probability':.8,'expected_value':.2,'calibrated':True,'eligible':True,'confidence_interval':[.75,.85]}


def test_scope_started_stale_duplicates_and_calibration_checks():
    now=datetime(2026,10,25,12,tzinfo=UTC);req=Request(5,('PL',),now.date(),'UTC');p=pick(now)
    bad=[pick(now,'FL1','other'),dict(p,calibrated=False),dict(p,selection='ah_home_0.5'),
         dict(p,updated_at=(now-timedelta(hours=1)).isoformat()),dict(p,probability=float('nan')),
         dict(p,fixture={**p['fixture'],'collected_at':(now-timedelta(seconds=1)).isoformat()}),
         dict(p,fixture={**p['fixture'],'collected_at':(now+timedelta(seconds=1)).isoformat()}),
         dict(p,fixture={**p['fixture'],'kickoff':now.isoformat()}),
         dict(p,fixture={**p['fixture'],'kickoff':(now+timedelta(days=1)).isoformat()})]
    assert select_picks(bad,req,now,600,now)==[]
    selected=select_picks([p,p],req,now,600,now)
    assert len(selected)==1
    text=render(selected,req)
    assert 'Only 1 of 5' in text and 'W1 (Home Win)' in text and 'Total Odds' not in text
    assert render([],req).startswith('SLIP UNAVAILABLE')


async def test_no_key_exits_honestly_without_network(engine):
    request=Request(2,('PL',),date(2026,10,25),'UTC')
    result=await generate(request,Settings(_env_file=None),engine)
    assert result['picks']==[] and 'FOOTBALL_DATA_TOKEN' in result['reason']


async def test_extra_markets_are_actual_quotes_not_derived(fixture,now):
    base={'id':'test-event','home_team':fixture.home_name,'away_team':fixture.away_name,'commence_time':fixture.kickoff.isoformat()}
    markets=[{'key':'btts','last_update':now.isoformat(),'outcomes':[{'name':'Yes','price':1.8},{'name':'No','price':2.1}]},
             {'key':'double_chance','last_update':now.isoformat(),'outcomes':[{'name':'1X','price':1.4},{'name':'unrecognized','price':8}]},
             {'key':'alternate_totals','last_update':now.isoformat(),'outcomes':[{'name':'Over','point':1.5,'price':1.2}]}]
    def response(r):
        if '/events/' in r.url.path:return httpx.Response(200,json={**base,'bookmakers':[{'title':'Synthetic book','key':'test','markets':markets}]})
        return httpx.Response(200,json=[{**base,'bookmakers':[]}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        quotes,gap=await ResearchProviders(client,Settings(odds_api_key='test',odds_extra_markets=True,_env_file=None)).odds(fixture)
    assert gap is None and {p['selection'] for p in quotes}=={'btts_yes','btts_no','1X','over_1.5'}
    assert next(p for p in quotes if p['selection']=='1X')['odds']==1.4
    assert all('/events/test-event/odds' in p['url'] and 'apiKey' not in p['url'] for p in quotes)


def test_official_short_name_for_ligue_one_cross_check(fixture):
    f=fixture.model_copy(update={'source':'football-data.org','league':'FL1','home_short_name':'Home Short','away_short_name':'Away Short'})
    e={'leagueName':'France Ligue 1','homeName':'Home Short','awayName':'Away Short','matchTime':f.kickoff.timestamp(),'status':0}
    assert agrees(e,f)
    assert not agrees({**e,'awayName':'Away Short Women'},f)


async def test_final_status_recheck_replaces_rejected_finalist(engine,monkeypatch):
    from betboot import terminal
    checked=[];fixtures={}
    request=Request(1,('PL',),(datetime.now(UTC)+timedelta(days=1)).date(),'UTC')
    async def scan(*args,**kwargs):
        assert kwargs['selected_leagues']==('PL',) and kwargs['day']==request.day.isoformat()
        assert kwargs['defer_odds'] is True
        now=datetime.now(UTC)
        rows=[]
        for event,team in [('first','1'),('second','3')]:
            p=pick(now,event=event)
            p['fixture'].update(home_id=team,away_id=str(int(team)+1),season=now.year,
                kickoff=(now+timedelta(days=1)).isoformat())
            fixtures[event]=p['fixture']
            rows.append({'fixture':p['fixture'],'ensemble':{},'recommendations':[p],'missing':[]})
        return {'id':'synthetic-scan','errors':[],'analyses':rows}
    async def current(self,path,params):
        event=path.split('/')[-1];checked.append(event)
        # The first fixture was postponed after analysis. The next candidate must be checked.
        f=fixtures[event]
        return {'id':event,'competition':{'code':'PL'},'homeTeam':{'id':f['home_id']},
                'awayTeam':{'id':f['away_id']},'status':'POSTPONED' if event=='first' else 'TIMED',
                'utcDate':fixtures[event]['kickoff']}
    monkeypatch.setattr(terminal,'scan',scan)
    monkeypatch.setattr(terminal,'model_report',lambda *args:{})
    monkeypatch.setattr(terminal.FootballDataProvider,'_get',current)
    result=await generate(request,Settings(football_data_token='synthetic',_env_file=None),engine)
    assert checked==['first','second']
    assert [p['fixture']['external_id'] for p in result['picks']]==['second']
    assert [v['passed'] for v in result['fixture_verifications']]==[False,True]


def test_slip_excludes_long_shots_and_prefers_probability():
    now=datetime(2026,10,25,12,tzinfo=UTC)
    req=Request(1,('PL',),now.date(),'UTC')
    base=pick(now)
    long_shot=dict(base,probability=.24,odds=5.8,expected_value=.392,confidence_interval=[.22,.27])
    assert select_picks([long_shot],req,now,600,now)==[]
    lower_probability=dict(base,probability=.70,odds=2,expected_value=.40,confidence_interval=[.65,.75])
    # The 80% candidate wins the ranking even with a smaller estimated edge.
    assert select_picks([lower_probability,base],req,now,600,now)==[base]
    assert select_picks([base],req,now,600,now,min_probability=.85)==[]


def test_empty_output_explains_fixture_coverage_and_quota():
    req=Request(6,('PL','PD'),date(2026,10,25),'UTC')
    text=render([],req,'The Odds API credits are exhausted.',coverage={'researched':2,'with_history':2})
    assert 'credits are exhausted' in text and 'Requested 6; researched 2' in text
    assert 'worldwide discovery' in text
