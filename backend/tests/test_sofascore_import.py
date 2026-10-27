from datetime import UTC,datetime,timedelta
import json
import pytest
from betboot.sofascore_import import new_capture,add_response,attach_ssr,repair_legacy,historical_summary


def test_target_identity_and_query_snapshots_preserved():
    c=new_capture('123')
    stamp=datetime.now(UTC).isoformat()
    assert not add_response(c,'https://www.sofascore.com/api/v1/event/1234',{'event':{'id':1234}})
    assert not add_response(c,'https://www.sofascore.com/api/v1/event/123',{'event':{'id':999}})
    for page in (0,1,0):
        assert add_response(c,f'https://www.sofascore.com/api/v1/event/123/statistics?page={page}',{},collected_at=stamp,status=200)
    assert len(c['responses'])==3
    assert c['responses'][1]['request_parameters']==[('page','1')]
    assert all(r['collected_at']==stamp for r in c['responses'])
    with pytest.raises(ValueError):add_response(c,'https://evil.example/api/v1/event/123',{})
    with pytest.raises(ValueError):add_response(c,'https://www.sofascore.com/api/v1/event/123',{},collected_at='2026-01-01T00:00:00')
    with pytest.raises(ValueError):add_response(c,'https://www.sofascore.com/api/v1/event/123',{},collected_at=(datetime.now(UTC)+timedelta(days=1)).isoformat())


def test_ssr_wrong_event_is_excluded():
    c=new_capture('123')
    assert not attach_ssr(c,{'props':{'pageProps':{'event':{'id':456}}}})
    assert c['next_data_ssr'] is None
    assert c['issues'][0]['actual_event_id']==456
    assert attach_ssr(c,{'props':{'pageProps':{'event':{'id':123}},'footerEntities':{'unrelated':True}}})
    assert 'footerEntities' not in str(c['next_data_ssr'])


def test_legacy_unknown_time_and_duplicate_all_period_stats():
    event={'id':123,'homeTeam':{'id':1,'name':'Synthetic home'},'awayTeam':{'id':2,'name':'Synthetic away'},
           'startTimestamp':1700000000,'status':{'type':'finished'},'tournament':{'name':'Synthetic league'}}
    item={'key':'expectedGoals','home':'3.50','away':'0.37'}
    periods=[{'period':'ALL','groups':[{'statisticsItems':[item,item]}]},
             {'period':'1ST','groups':[{'statisticsItems':[{'key':'expectedGoals','home':'1.98','away':'0.11'}]}]}]
    raw={'event_id':'123','api_responses':{'event/123':{'event':event},'event/123/statistics':{'statistics':periods},
         'country/alpha2':{'private':'unrelated'}},'next_data_ssr':{'props':{'pageProps':{'event':{'id':456}}}}}
    original=json.dumps(raw).encode(); c=repair_legacy(raw,original); report=historical_summary(c)
    assert len(c['responses'])==2 and c['excluded_response_count']==1
    assert all(r['collected_at'] is None and r['request_parameters'] is None and r['url_reconstructed'] for r in c['responses'])
    assert report['statistics']['expectedGoals']=={'home':3.5,'away':.37}
    assert not report['pre_match_eligible'] and not report['collection_time_known']
    assert json.dumps(raw).encode()==original
    assert 'private' not in json.dumps(c)


def test_conflicting_duplicate_stats_not_silently_selected():
    c=new_capture('123')
    event={'id':123,'homeTeam':{'id':1,'name':'A'},'awayTeam':{'id':2,'name':'B'},'startTimestamp':1700000000,
           'status':{'type':'finished'},'tournament':{'name':'Test'}}
    add_response(c,'https://www.sofascore.com/api/v1/event/123',{'event':event})
    add_response(c,'https://www.sofascore.com/api/v1/event/123/statistics',{'statistics':[{'period':'ALL','groups':[{'statisticsItems':[
        {'key':'expectedGoals','home':'1','away':'2'},{'key':'expectedGoals','home':'3','away':'2'}]}]}]})
    assert 'expectedGoals' not in historical_summary(c)['statistics']
