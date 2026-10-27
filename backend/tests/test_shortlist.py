from datetime import UTC,datetime,timedelta
import json
import httpx
from betboot.config import Settings
from betboot.shortlist import research_candidates,take_research
from betboot.terminal import Request,render
from betboot.gemini import review


def data(now,count=5):
    rows=[]
    for i in range(count):
        f={'source':'football-data.org','external_id':str(i),'league':'PL','status':'TIMED','season':now.year,
           'home_id':str(i*2),'away_id':str(i*2+1),'home_name':f'Test {i*2}','away_name':f'Test {i*2+1}',
           'kickoff':(now+timedelta(hours=2)).isoformat(),'collected_at':now.isoformat()}
        rows.append({'fixture':f,'baseline':{'home_goal_rate':1.5},'ensemble':{'1X':.8,'away':.2},
                     'quotes':[{'selection':'1X','odds':1.5,'bookmaker':'Synthetic','updated_at':now.isoformat()}],
                     'comparisons':[],'sources':[],'missing':['Shot-based xG/xGA']})
    return rows


def test_five_research_forecasts_are_not_five_value_bets():
    now=datetime(2026,10,25,12,tzinfo=UTC);req=Request(5,('PL',),now.date(),'UTC')
    candidates=research_candidates(data(now),req,now,now,600,.65)
    selected=take_research(candidates,[],req,now,now,600)
    assert len(selected)==5 and all(p['research_only'] and not p['eligible'] for p in selected)
    output=render([],req,research=selected)
    assert 'MATCH SHORTLIST' in output and output.count('Model lean:')==5
    assert '80%' not in output and 'Unvalidated forecast' in output and 'no verified edge' in output


def test_shortlist_rejects_wrong_scope_stale_started_and_no_history():
    now=datetime(2026,10,25,12,tzinfo=UTC);req=Request(5,('PL',),now.date(),'UTC');rows=data(now)
    rows[0]['fixture']['league']='PD'
    rows[1]['fixture']['kickoff']=now.isoformat()
    rows[2]['fixture']['collected_at']=(now-timedelta(minutes=5)).isoformat()
    rows[3]['baseline']={}
    rows[4]['quotes']=[]
    candidates=research_candidates(rows,req,now,now,600,.65)
    assert candidates==[]


async def test_gemini_only_accepts_existing_gap_references():
    now=datetime(2026,10,25,12,tzinfo=UTC);req=Request(1,('PL',),now.date(),'UTC')
    picks=research_candidates(data(now,1),req,now,now,600,.65)
    settings=Settings(gemini_api_key='synthetic',gemini_model='test-model',_env_file=None)
    def response(r):
        assert r.headers['x-goog-api-key']=='synthetic' and 'synthetic' not in str(r.url)
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps({'reviews':[{'id':'0','gap_indices':[0]}]})}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        result=await review(client,settings,picks)
    assert result['status']=='complete' and result['reviews'][0]['gaps']==['Shot-based xG/xGA']
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'{"reviews":[{"id":"invented","gap_indices":[9]}]}'}]}}]}))) as client:
        assert (await review(client,settings,picks))['status']=='unavailable'
    assert (await review(None,Settings(_env_file=None),picks))['status']=='not_configured'


async def test_generator_verifies_and_returns_five_research_matches(engine,monkeypatch):
    from betboot import terminal
    now=datetime.now(UTC);req=Request(5,('PL',),(now+timedelta(days=1)).date(),'UTC');rows=[];checked=[]
    async def scan(*args,**kwargs):
        rows.extend(data(datetime.now(UTC)))
        for a in rows:
            a['fixture']['kickoff']=(now+timedelta(days=1)).isoformat()
            a.update(ensemble={},recommendations=[])
        return {'analyses':rows,'errors':[],'id':'synthetic'}
    def candidates(*args):
        return [{'fixture':a['fixture'],'selection':'1X','probability':.8,'calibrated':False,
                 'research_only':True,'eligible':False,'odds':1.5,'updated_at':a['fixture']['collected_at'],'missing':[]} for a in rows]
    async def current(self,path,params):
        f=rows[int(path.split('/')[-1])]['fixture'];checked.append(f['external_id'])
        return {'id':f['external_id'],'competition':{'code':'PL'},'homeTeam':{'id':f['home_id']},
                'awayTeam':{'id':f['away_id']},'status':'TIMED','utcDate':f['kickoff']}
    monkeypatch.setattr(terminal,'scan',scan)
    monkeypatch.setattr(terminal,'research_candidates',candidates)
    monkeypatch.setattr(terminal,'model_report',lambda *a:{})
    monkeypatch.setattr(terminal.FootballDataProvider,'_get',current)
    result=await terminal.generate(req,Settings(football_data_token='synthetic',_env_file=None),engine)
    assert len(result['research_shortlist'])==5 and not result['picks'] and len(checked)==5


def test_missing_odds_cannot_satisfy_minimum_price():
    now=datetime(2026,10,25,12,tzinfo=UTC);req=Request(4,('PL',),now.date(),'UTC')
    rows=data(now,4)
    for a in rows:a['quotes']=[]
    assert research_candidates(rows,req,now,now,600,.65)==[]


def test_price_floor_before_ranking_and_one_selection_output():
    now=datetime(2026,10,25,12,tzinfo=UTC);req=Request(1,('PL',),now.date(),'UTC')
    rows=data(now,1);a=rows[0]
    a['ensemble']={'1X':.9,'over_2.5':.7}
    a['quotes'][0]['odds']=1.49
    a['quotes'].append({'selection':'over_2.5','odds':1.5,'bookmaker':'Synthetic','updated_at':now.isoformat()})
    selected=research_candidates(rows,req,now,now,600,.65)
    assert len(selected)==1 and selected[0]['selection']=='over_2.5'
    assert selected[0]['odds']==1.5
    assert 'Market alternatives' not in render([],req,research=selected)
    assert 'Market alternatives' in render([],req,research=selected,details=True)
    a['quotes'][1]['odds']=1.49
    assert research_candidates(rows,req,now,now,600,.65)==[]


def test_alternative_odds_expire_before_output():
    now=datetime(2026,10,25,12,tzinfo=UTC);req=Request(1,('PL',),now.date(),'UTC')
    candidates=research_candidates(data(now,1),req,now,now,600,.65)
    selected=take_research(candidates,[],req,now+timedelta(minutes=11),now,600)
    assert selected==[]


async def test_gemini_ranks_only_existing_candidates_and_rejects_invented_ids():
    from betboot.gemini import rank_candidates
    now=datetime(2026,10,25,12,tzinfo=UTC);req=Request(2,('PL',),now.date(),'UTC')
    candidates=research_candidates(data(now,2),req,now,now,600,.65)
    settings=Settings(gemini_api_key='synthetic',gemini_model='test-model',_env_file=None)
    def response(ids):
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps({'ordered_ids':ids})}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:response(['1','0']))) as client:
        ranked,audit=await rank_candidates(client,settings,candidates)
    assert ranked==list(reversed(candidates)) and audit['status']=='complete'
    for ids in (['invented','0'],['0','0'],['0']):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:response(ids))) as client:
            ranked,audit=await rank_candidates(client,settings,candidates)
        assert ranked==candidates and audit['status']=='unavailable'


def test_same_provider_world_odds_are_not_independent_corroboration():
    now=datetime(2026,10,25,12,tzinfo=UTC);req=Request(1,('*',),now.date(),'UTC')
    rows=data(now,1);rows[0]['fixture'].update(source='isportsapi.com',league='IS144',country='Brazil')
    rows[0]['quotes'][0]['source']='iSports API'
    candidates=research_candidates(rows,req,now,now,600,.65)
    assert len(candidates)==1 and candidates[0]['fixture_cross_checked'] is False
    rows[0]['quotes'][0]['source']='The Odds API'
    assert research_candidates(rows,req,now,now,600,.65)[0]['fixture_cross_checked'] is True
