from datetime import timedelta

from betboot.backtest import historical_validation, replay_probabilities


def test_replay_cannot_use_target_or_later_results(fixture,history,now):
    cutoff=now
    original=replay_probabilities(fixture,history,cutoff)
    future=fixture.model_copy(update={'status':'FINISHED','home_goals':99,'away_goals':0})
    assert original==replay_probabilities(fixture,history+[future],cutoff)
    assert original is not None


def test_replay_preserves_provenance_and_separates_calibration_test(fixture,now):
    rows=[fixture.model_copy(update={'external_id':str(i),'status':'FINISHED',
          'kickoff':now-timedelta(days=300-i),'collected_at':now-timedelta(minutes=1),
          'home_goals':i%4,'away_goals':i%3}) for i in range(250)]
    snapshots=[m.model_dump(mode='json') for m in rows]
    report=historical_validation(rows,now,'PL')
    assert report['recommendation_eligible'] is False
    assert report['mode']=='retrospective latest-download replay'
    assert report['markets']
    assert report['split']['calibration_last_kickoff']<report['split']['test_start']
    assert report['split']['test_count']>=40
    assert snapshots==[m.model_dump(mode='json') for m in rows]
    assert report==historical_validation(list(reversed(rows)),now,'PL')


def test_small_sample_does_not_claim_validation(fixture,history,now):
    report=historical_validation(history,now,'PL')
    assert report['markets']=={}
    assert report['recommendation_eligible'] is False


def test_historical_gate_requires_fresh_large_passing_sample(now):
    from betboot.backtest import VERSION,historical_probability
    report={'version':VERSION,'created_at':now.isoformat(),'dataset_hash':'test-only',
      'markets':{'home':{'diagnostic_pass':True,'calibrated':{'n':250},
        'calibrator':{'x':[0,1],'y':[.7,.7]},'bins':[{'low':.6,'high':.8,'n':70,'interval':[.6,.8]}]}}}
    assert historical_probability(report,'home',.5,now)['validation_mode']=='historical_holdout'
    assert historical_probability(report,'home',.5,now+timedelta(days=8)) is None
    report['markets']['home']['calibrated']['n']=30
    assert historical_probability(report,'home',.5,now) is None
    report['markets']['home']['calibrated']['n']=250
    report['markets']['home']['diagnostic_pass']=False
    assert historical_probability(report,'home',.5,now) is None
