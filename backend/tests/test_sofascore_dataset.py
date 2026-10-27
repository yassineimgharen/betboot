import json
from datetime import timedelta
import pytest
from betboot.sofascore_import import new_capture,add_response
from betboot.sofascore_dataset import import_files,records,normalize_capture,digest
from betboot.models.imported_xg import features,validate,calibrated,fingerprint,VERSION
from betboot.models.poisson import InsufficientData


def capture(now):
    c=new_capture('123')
    event={'id':123,'homeTeam':{'id':1,'name':'Test A'},'awayTeam':{'id':2,'name':'Test B'},
           'startTimestamp':int((now-timedelta(days=1)).timestamp()),'status':{'type':'finished'},
           'homeScore':{'normaltime':2},'awayScore':{'normaltime':1},'season':{'id':99,'year':'2026'},
           'tournament':{'name':'Premier League','uniqueTournament':{'id':17,'name':'Premier League','category':{'name':'England','sport':{'slug':'football'}}}}}
    add_response(c,'https://www.sofascore.com/api/v1/event/123',{'event':event})
    add_response(c,'https://www.sofascore.com/api/v1/event/123/statistics',{'statistics':[{'period':'ALL','groups':[{'statisticsItems':[{'key':'expectedGoals','home':'1.5','away':'0.7'}]}]}]})
    return c


def test_import_idempotence_missing_time_and_atomic_rejection(engine,tmp_path,now):
    c=capture(now);p=tmp_path/'capture.json';p.write_text(json.dumps(c))
    assert import_files(engine,[p])['imported']==1
    assert import_files(engine,[p])['duplicates']==1
    row=records(engine)[0]
    assert row['captured_at'] is None and row['available_at']==row['imported_at']
    c['responses'][0]['data']['event']['homeScore']={'current':2}
    p.write_text(json.dumps(c))
    assert len(import_files(engine,[p])['rejected'])==1
    assert len(records(engine))==1


def test_import_revalidates_v2_identity_and_timing(now):
    c=capture(now);c['responses'][0]['data']['event']['id']=999
    with pytest.raises(ValueError):normalize_capture(c,json.dumps(c).encode(),now)
    c=capture(now);c['responses'][1]['collected_at']=(now-timedelta(days=2)).isoformat()
    with pytest.raises(ValueError,match='pre-kickoff'):normalize_capture(c,json.dumps(c).encode(),now)


def history(now,n=600):
    rows=[]
    for i in range(n):
        kickoff=now-timedelta(days=n-i)
        row={'event_id':str(i),'league_id':'17','league_name':'Premier League','country':'England',
             'home_id':'1','away_id':'2','home_name':'Test A','away_name':'Test B',
             'kickoff':kickoff.isoformat(),'available_at':(kickoff+timedelta(hours=3)).isoformat(),
             'imported_at':(kickoff+timedelta(hours=3)).isoformat(),'capture_time_known':True,
             'home_xg':1.3+(i%4)/10,'away_xg':.8+(i%3)/10,'home_goals':i%4,'away_goals':i%3,
             'source_urls':['https://www.sofascore.com/api/v1/event/'+str(i)]}
        row['fingerprint']=digest(row);rows.append(row)
    return rows


def test_features_exclude_late_data_future_games_and_target(now):
    rows=history(now,80);original=features(rows,'17','1','2',now)
    assert original['form']['home']['matches']==10
    assert original['form']['home']['venue_matches']==20
    assert sum(original['probabilities'][k] for k in ('home','draw','away'))==pytest.approx(1)
    late={**rows[-1],'event_id':'late','available_at':(now+timedelta(days=1)).isoformat(),'home_xg':19}
    future={**rows[-1],'event_id':'future','kickoff':(now+timedelta(days=1)).isoformat(),'home_xg':19}
    assert features(rows+[late,future],'17','1','2',now)==original
    assert rows[-1]['event_id'] not in features(rows,'17','1','2',now,target_id=rows[-1]['event_id'])['training_ids']
    with pytest.raises(InsufficientData):features(rows,'17','missing','2',now)


def test_validation_is_chronological_and_unknown_downloads_do_not_backdate(now):
    rows=history(now)
    report=validate(rows,'17',now)
    assert report['forecasts']==550 and report['test_count']==220
    assert report['calibration_count']==330
    assert report['markets'] and report['version']==VERSION
    unknown=[{**r,'available_at':now.isoformat(),'imported_at':now.isoformat(),'capture_time_known':False} for r in rows]
    assert validate(unknown,'17',now)['forecasts']==0


def test_xg_gate_rejects_stale_changed_or_small_validation(now):
    rows=history(now,80)
    report={'version':VERSION,'league_id':'17','as_of':now.isoformat(),'dataset_fingerprint':fingerprint(rows,'17'),
            'test_count':200,'markets':{'17:home':{'eligible':True,'calibrator':{'x':[0,1],'y':[.9,.9]},
            'bins':[{'low':.8,'high':1,'n':50,'interval':[.85,.95]}]}}}
    assert calibrated(report,rows,'17','home',.8,now)['probability']==.9
    assert calibrated({**report,'as_of':(now-timedelta(days=8)).isoformat()},rows,'17','home',.8,now) is None
    assert calibrated({**report,'test_count':199},rows,'17','home',.8,now) is None
    assert calibrated(report,rows+[dict(rows[0],fingerprint='changed')],'17','home',.8,now) is None


def test_xg_predictions_use_separate_validation_and_keep_context_gates(fixture,history,now,monkeypatch):
    from betboot.pipeline import research_analysis
    model={'available':True,'league_id':'17','probabilities':{'away':.92},'sources':['https://www.sofascore.com/api/v1/event/123'],
           'training_count':60,'training_ids':['123'],'training_fingerprint':'test-only','formula':'Synthetic test model'}
    monkeypatch.setattr('betboot.pipeline.imported_fixture_model',lambda *args:model)
    monkeypatch.setattr('betboot.pipeline.calibrated_probability',lambda *args:None)
    quote={'selection':'away','odds':1.2,'updated_at':now.isoformat(),'bookmaker':'Synthetic test book','url':'https://example.com'}
    evidence=[{'kind':k,'source':'Synthetic only','url':'https://example.com','collected_at':now.isoformat()} for k in ('injuries','lineups')]
    args=(fixture,history,now,[quote],evidence,True,[],{})
    # A raw xG estimate alone never becomes a bet.
    monkeypatch.setattr('betboot.pipeline.imported_calibrated',lambda *args:None)
    assert not research_analysis(*args,xg_rows=[{}])['recommendations']
    monkeypatch.setattr('betboot.pipeline.imported_calibrated',lambda *args:{'probability':.92,'interval':[.9,.95],'interval_method':'test cohort','calibration_bin_n':50})
    result=research_analysis(*args,xg_rows=[{}],min_probability=.9)
    assert result['recommendations'][0]['raw_model']=='imported xG Poisson'
    assert result['recommendations'][0]['validation_mode']=='imported_xg_chronological_holdout'
    assert not research_analysis(fixture,history,now,[quote],[],False,[],{},xg_rows=[{}])['recommendations']
