from datetime import timedelta

import httpx
import numpy as np
import pytest

from betboot.analytics import advanced_models, handicap_distribution, handicap_value, score_grid
from betboot.config import Settings
from betboot.evaluation import evaluate, wilson, calibrated_probability
from betboot.models.poisson import predict
from betboot.pipeline import research_analysis, window, scan
from betboot.research import ResearchProviders, matched
from betboot.store import read, save


def test_window_dst_and_invalid_inputs():
    start,end=window('2026-11-01',24,'America/New_York')
    assert (end-start).total_seconds()==25*3600
    with pytest.raises(ValueError): window(hours=30)
    with pytest.raises(ValueError): window(timezone='not/a/zone')


def test_handicap_quarter_push_and_half_settlement():
    grid=np.zeros((3,3));grid[1,1]=1
    d=handicap_distribution(grid,-.25)
    assert d['-0.5']==1
    assert handicap_value(d,2)['expected_value']==-.5
    assert handicap_distribution(grid,0)['0.0']==1
    assert handicap_distribution(grid,.25)['0.5']==1
    assert handicap_value(handicap_distribution(grid,.25),2)['expected_value']==.5
    with pytest.raises(ValueError):handicap_distribution(grid,.3)


def test_dc_normalization_and_advanced_no_future_leak(fixture,history,now):
    base=predict(fixture,history,now)
    result=advanced_models(history,fixture,base,now)
    for model in result['models'].values():
        assert sum(model[k] for k in ('home','draw','away'))==pytest.approx(1)
    assert score_grid(1.4,1.2,-.05).sum()==pytest.approx(1)
    future=history[0].model_copy(update={'home_goals':100,'collected_at':now+timedelta(days=1)})
    assert result==advanced_models(history+[future],fixture,base,now)


def test_strict_identity_no_fuzzy_or_reversed_join(fixture):
    e={'home_team':fixture.home_name,'away_team':fixture.away_name,'commence_time':fixture.kickoff.isoformat()}
    assert matched(e,fixture)
    assert not matched({**e,'home_team':fixture.home_name+' FC'},fixture)
    assert not matched({**e,'home_team':fixture.away_name,'away_team':fixture.home_name},fixture)


async def test_odds_freshness_margin_and_no_secret_in_sources(fixture,now):
    event={'id':'odds1','home_team':fixture.home_name,'away_team':fixture.away_name,'commence_time':fixture.kickoff.isoformat(),
           'bookmakers':[{'key':'test','title':'Synthetic test book','last_update':now.isoformat(),
             'markets':[{'key':'h2h','outcomes':[{'name':fixture.home_name,'price':2.5},{'name':'Draw','price':3.2},{'name':fixture.away_name,'price':2.8}]}]}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=[event]))) as client:
        p=ResearchProviders(client,Settings(odds_api_key='private-secret',_env_file=None))
        quotes,error=await p.odds(fixture)
        assert error is None and len(quotes)==3
        assert sum(q['margin_removed_probability'] for q in quotes)==pytest.approx(1)
        assert 'private-secret' not in str(quotes)
        event['bookmakers'][0]['last_update']=(now-timedelta(hours=1)).isoformat()
        p.odds_cache.clear()
        quotes,_=await p.odds(fixture)
        assert quotes==[]


def test_research_abstains_and_distinguishes_estimates(fixture,history,now):
    result=research_analysis(fixture,history,now,[],[],False,[],{})
    assert result['status']=='NO BET'
    assert result['recommendations']==[]
    assert result['ensemble']
    assert result['sources'][0]['url'].startswith('https://')
    assert result['reliability']['score']==40


def test_stale_after_context_research_is_rejected(fixture,history,now):
    quote={'selection':'home','odds':3.,'updated_at':(now-timedelta(hours=1)).isoformat()}
    result=research_analysis(fixture,history,now,[quote],[],True,[],{})
    assert result['quotes']==[]


def test_intervals_and_calibration_gates():
    lo,hi=wilson(50,100)
    assert lo<.5<hi
    assert calibrated_probability({},'PL','home',.7) is None
    assert wilson(0,0)==[0.,1.]


async def test_no_credentials_scan_is_honest_and_persisted(engine):
    result=await scan(engine,Settings(_env_file=None))
    assert result['analyses']==[]
    assert result['recommendations']==[]
    assert result['errors']
    assert read(engine,'scan')[0]['id']==result['id']


def test_prospective_calibration_never_uses_future_predictions(fixture,now):
    from betboot.analytics import VERSION
    predictions=[];results=[]
    for i in range(140):
        t=now-timedelta(days=150-i)
        f=fixture.model_copy(update={'external_id':str(i),'kickoff':t+timedelta(hours=2),'collected_at':t-timedelta(minutes=5)})
        p=.75 if i%2 else .25
        predictions.append({'analysis_version':VERSION,'fixture':f.model_dump(mode='json'),'as_of':t.isoformat(),'ensemble':{'home':p}})
        results.append(f.model_copy(update={'status':'FINISHED','home_goals':2 if i%2 else 0,'away_goals':1,'collected_at':t+timedelta(hours=5)}))
    report=evaluate(predictions,results,now)
    assert report['sample_count']==140
    assert report['markets']['PL:home']['metrics']['n']==80
    assert report['markets']['PL:home']['eligible']
    later={**predictions[-1],'as_of':(now+timedelta(days=1)).isoformat()}
    assert report==evaluate(predictions+[later],results,now)
    # Re-polling historical results today must not erase their earlier availability.
    repolled=[m.model_copy(update={'collected_at':now}) for m in results]
    assert report==evaluate(predictions,results+repolled,now)


def test_gradient_boosting_trains_only_when_supported(fixture,now):
    history=[fixture.model_copy(update={'external_id':str(i),'status':'FINISHED',
            'kickoff':now-timedelta(days=250-i),'collected_at':now-timedelta(days=249-i),
            'home_goals':i%4,'away_goals':i%3}) for i in range(200)]
    base=predict(fixture,history,now)
    result=advanced_models(history,fixture,base,now)
    assert 'gradient_boosting' in result['models']
    assert sum(result['models']['gradient_boosting'].values())==pytest.approx(1,abs=1e-6)
    assert result['calibrated'] is False


def test_recommendations_require_calibration_context_and_lower_bound_edge(fixture,history,now):
    quote={'selection':'home','odds':2.,'updated_at':now.isoformat(),'bookmaker':'Synthetic test book',
           'url':'https://example.com/test-only'}
    evidence=[{'kind':kind,'source':'Synthetic test only','url':'https://example.com','collected_at':now.isoformat()} for kind in ('injuries','lineups')]
    validation={'markets':{'PL:home':{'eligible':True,'calibrator':{'x':[0,1],'y':[.8,.8]},
                'bins':[{'low':.8,'high':1.,'n':100,'interval':[.7,.9]}]}}}
    result=research_analysis(fixture,history,now,[quote],evidence,True,[],validation)
    assert result['status']=='REVIEW'
    assert len(result['recommendations'])==1
    assert result['recommendations'][0]['expected_value']==pytest.approx(.6)
    assert research_analysis(fixture,history,now,[quote],[],True,[],validation)['status']=='NO BET'
    assert research_analysis(fixture,history,now,[quote],evidence,False,[],validation)['status']=='NO BET'


async def test_paper_results_do_not_duplicate_a_repeated_scan(engine,fixture,now):
    from betboot.pipeline import paper_results
    from betboot.collection import collect
    from test_collection import StubProvider
    f=fixture.model_copy(update={'kickoff':now-timedelta(days=1)})
    recommendation={'fixture':f.model_dump(mode='json'),'selection':'home','odds':2.0}
    save(engine,'scan',{'recommendations':[recommendation]})
    save(engine,'scan',{'recommendations':[recommendation]})
    result=f.model_copy(update={'status':'FINISHED','home_goals':2,'away_goals':1,'collected_at':now})
    await collect(engine,StubProvider([result]),f.league,f.season)
    report=paper_results(engine)
    assert report['settled']==1 and report['profit_units']==1
    assert report['yield']==1


async def test_context_rate_limit_applies_to_each_call_and_retry(monkeypatch):
    from unittest.mock import AsyncMock
    from betboot.research import get_json
    sleep=AsyncMock()
    monkeypatch.setattr('betboot.research.asyncio.sleep',sleep)
    replies=iter([httpx.Response(503),httpx.Response(200,json={'response':[]})])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:next(replies))) as client:
        assert await get_json(client,'https://v3.football.api-sports.io/fixtures')=={'response':[]}
    assert sum(call.args==(6.1,) for call in sleep.await_args_list)==2


async def test_quota_exhaustion_pauses_provider_for_remaining_scan(engine,fixture,monkeypatch):
    from unittest.mock import AsyncMock
    from betboot.collection import collect
    from betboot.providers.base import ProviderError
    from test_collection import StubProvider
    second=fixture.model_copy(update={'external_id':'second-test','kickoff':fixture.kickoff+timedelta(hours=1)})
    await collect(engine,StubProvider([fixture,second]),fixture.league,fixture.season)
    odds=AsyncMock(side_effect=ProviderError('Research provider quota exhausted; retry on a later scan'))
    monkeypatch.setattr('betboot.pipeline.ResearchProviders.odds',odds)
    result=await scan(engine,Settings(_env_file=None),hours=72)
    assert odds.await_count==1
    assert len(result['analyses'])==2
    assert any('paused' in gap for gap in result['analyses'][1]['missing'])


def test_historical_calibration_is_labelled_and_does_not_skip_context(fixture,history,now):
    from betboot.backtest import VERSION
    quote={'selection':'home','odds':2.,'updated_at':now.isoformat(),'bookmaker':'Synthetic test only','url':'https://example.com'}
    evidence=[{'kind':k,'source':'Synthetic test only','url':'https://example.com','collected_at':now.isoformat()} for k in ('injuries','lineups')]
    historical={'version':VERSION,'league':'PL','created_at':now.isoformat(),'dataset_hash':'test-only','markets':{
        'home':{'diagnostic_pass':True,'calibrated':{'n':250},'calibrator':{'x':[0,1],'y':[.8,.8]},
        'bins':[{'low':.8,'high':1.,'n':100,'interval':[.7,.9]}]}}}
    result=research_analysis(fixture,history,now,[quote],evidence,True,[],{},historical=historical)
    assert result['status']=='REVIEW'
    assert result['recommendations'][0]['validation_mode']=='historical_holdout'
    assert 'not prospective' in result['recommendations'][0]['warning']
    assert research_analysis(fixture,history,now,[quote],[],True,[],{},historical=historical)['status']=='NO BET'


def test_explicit_odds_aliases_require_correct_identity_and_kickoff(fixture):
    f=fixture.model_copy(update={'source':'football-data.org','home_id':'57','away_id':'62',
                                'home_name':'Arsenal FC','away_name':'Everton FC'})
    e={'home_team':'Arsenal','away_team':'Everton','commence_time':f.kickoff.isoformat()}
    assert matched(e,f,provider='the-odds-api')
    assert not matched(e,f)
    assert not matched(e,f.model_copy(update={'home_id':'65'}),provider='the-odds-api')
    assert not matched({**e,'commence_time':(f.kickoff+timedelta(hours=2)).isoformat()},f,provider='the-odds-api')
    assert not matched({**e,'home_team':'Arsenal Women'},f,provider='the-odds-api')


async def test_alias_matched_event_uses_provider_outcome_names(fixture,now):
    f=fixture.model_copy(update={'source':'football-data.org','home_id':'57','away_id':'62',
                                'home_name':'Arsenal FC','away_name':'Everton FC'})
    event={'id':'synthetic-test','home_team':'Arsenal','away_team':'Everton','commence_time':f.kickoff.isoformat(),
           'bookmakers':[{'key':'test','title':'Synthetic test only','last_update':now.isoformat(),
             'markets':[{'key':'h2h','outcomes':[{'name':'Arsenal','price':2.0},{'name':'Draw','price':4.0},{'name':'Everton','price':4.0}]}]}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=[event]))) as client:
        p=ResearchProviders(client,Settings(odds_api_key='test-only',_env_file=None))
        quotes,error=await p.odds(f)
        assert error is None
        assert {q['selection'] for q in quotes}=={'home','draw','away'}
        assert sum(q['margin_removed_probability'] for q in quotes)==pytest.approx(1)
        p.odds_cache['soccer_epl']=[event,event]
        quotes,error=await p.odds(f)
        assert not quotes and 'unique' in error


def test_positive_cohort_bound_cannot_override_negative_estimated_ev(fixture,history,now,monkeypatch):
    # A held-out cohort interval need not contain the frozen calibrated estimate.
    monkeypatch.setattr('betboot.pipeline.calibrated_probability',lambda *args: {
        'probability':.2,'interval':[.3,.45],'interval_method':'cohort Wilson','calibration_bin_n':100})
    quote={'selection':'away','odds':4.,'updated_at':now.isoformat(),'bookmaker':'Synthetic test book',
           'url':'https://example.com/test-only'}
    evidence=[{'kind':k,'source':'Synthetic test only','url':'https://example.com','collected_at':now.isoformat()} for k in ('injuries','lineups')]
    result=research_analysis(fixture,history,now,[quote],evidence,True,[],{})
    assert result['comparisons'][0]['expected_value']==pytest.approx(-.2)
    assert result['status']=='NO BET' and not result['recommendations']


def test_probability_target_filters_before_selecting_one_market(fixture,history,now,monkeypatch):
    def calibration(validation,league,market,raw):
        return {'probability':.95 if market=='away' else .8,
                'interval':[.9,.98] if market=='away' else [.7,.9],
                'interval_method':'test cohort','calibration_bin_n':100}
    monkeypatch.setattr('betboot.pipeline.calibrated_probability',calibration)
    quotes=[{'selection':selection,'odds':odds,'updated_at':now.isoformat(),
             'bookmaker':'Synthetic test book','url':'https://example.com/test-only'}
             for selection,odds in [('home',2.),('away',1.2)]]
    evidence=[{'kind':kind,'source':'Synthetic test only','url':'https://example.com','collected_at':now.isoformat()} for kind in ('injuries','lineups')]
    result=research_analysis(fixture,history,now,quotes,evidence,True,[],{},min_probability=.9)
    assert result['recommendations'][0]['selection']=='away'
    assert result['recommendations'][0]['probability']==.95
    assert not research_analysis(fixture,history,now,quotes[:1],evidence,True,[],{},min_probability=.9)['recommendations']
    assert not research_analysis(fixture,history,now,quotes,[],True,[],{},min_probability=.9)['recommendations']


async def test_odds_credit_exhaustion_is_explicit_and_stops_repeated_requests(fixture):
    from betboot.providers.base import ProviderError
    calls=[]
    def response(request):
        calls.append(request.url.path)
        return httpx.Response(401,json={'error_code':'OUT_OF_USAGE_CREDITS','message':'Do not expose private-secret'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        provider=ResearchProviders(client,Settings(odds_api_key='private-secret',_env_file=None))
        for f in (fixture,fixture.model_copy(update={'league':'PD'})):
            with pytest.raises(ProviderError,match='quota exhausted') as error:
                await provider.odds(f)
            assert 'private-secret' not in str(error.value)
    assert len(calls)==1
