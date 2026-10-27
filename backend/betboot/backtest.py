"""Explicit retrospective validation, never represented as archived live predictions."""
import hashlib
import json
import math
from datetime import datetime, timedelta

import numpy as np
from sklearn.isotonic import IsotonicRegression

from betboot.analytics import outcome
from betboot.domain import require_aware
from betboot.evaluation import metrics, wilson
from betboot.models.poisson import ModelConfig, markets


VERSION = 'historical-poisson-v1'


def historical_probability(report, market, raw, now):
    """Conservative use of a frozen historical calibrator, explicitly distinguished
    from prospective validation. Never claims historical price or profit validation.
    """
    if not report or report.get('version')!=VERSION:
        return None
    created=datetime.fromisoformat(report['created_at'])
    if not timedelta(0)<=now-created<=timedelta(days=7):
        return None
    m=report.get('markets',{}).get(market,{})
    if not m.get('diagnostic_pass') or m.get('calibrated',{}).get('n',0)<200:
        return None
    p=float(np.interp(raw,m['calibrator']['x'],m['calibrator']['y']))
    b=next((b for b in m['bins'] if b['low']<=p<b['high'] or p==1 and b['high']==1),None)
    if not b or b['n']<50:
        return None
    return {'probability':p,'interval':b['interval'],'calibration_bin_n':b['n'],
            'interval_method':'95% Wilson interval for historical held-out calibration-bin frequency',
            'validation_mode':'historical_holdout','dataset_hash':report['dataset_hash']}


def replay_probabilities(target, history, cutoff, config=None):
    """Latest-download replay: assume results available six hours after kickoff.

    Real collection timestamps are NEVER changed. This is not a point-in-time database.
    Only final outcomes of matches earlier than the assumed availability cutoff are used.
    """
    require_aware(cutoff)
    config=config or ModelConfig()
    rows=[m for m in history if m.source==target.source and m.league==target.league and
          m.status=='FINISHED' and m.external_id!=target.external_id and
          cutoff-timedelta(days=config.lookback_days)<=m.kickoff and
          m.kickoff+timedelta(hours=6)<cutoff]
    home=[m for m in rows if m.home_id==target.home_id]
    away=[m for m in rows if m.away_id==target.away_id]
    if len(rows)<config.min_league_matches or min(len(home),len(away))<config.min_venue_matches:
        return None
    if any(not any(team in (m.home_id,m.away_id) and cutoff-m.kickoff<=timedelta(days=config.max_result_age_days)
                   for m in rows) for team in (target.home_id,target.away_id)):
        return None
    def weight(m):return .5**((cutoff-m.kickoff).total_seconds()/86400/config.half_life_days)
    total=sum(weight(m) for m in rows)
    hmean=sum(weight(m)*m.home_goals for m in rows)/total
    amean=sum(weight(m)*m.away_goals for m in rows)/total
    if min(hmean,amean)<=0:return None
    def rate(sample,field,prior):
        return (sum(weight(m)*getattr(m,field) for m in sample)+config.prior_matches*prior)/(sum(weight(m) for m in sample)+config.prior_matches)
    h=rate(home,'home_goals',hmean)*rate(away,'home_goals',hmean)/hmean
    a=rate(away,'away_goals',amean)*rate(home,'away_goals',amean)/amean
    if max(h,a)>20:return None
    return markets(h,a)


def historical_validation(observations, as_of, league):
    require_aware(as_of)
    latest={}
    for m in sorted(observations,key=lambda m:m.collected_at):
        if m.collected_at<=as_of and m.league==league:
            latest[(m.source,m.external_id)]=m
    rows=sorted((m for m in latest.values() if m.status=='FINISHED' and m.kickoff<as_of),key=lambda m:(m.kickoff,m.source,m.external_id))
    dataset_hash=hashlib.sha256(json.dumps([m.model_dump(mode='json') for m in rows],sort_keys=True).encode()).hexdigest()
    forecasts=[];skipped=0
    for match in rows:
        cutoff=match.kickoff-timedelta(hours=1)
        p=replay_probabilities(match,rows,cutoff)
        if p is None:
            skipped+=1;continue
        forecasts.append({'match':match,'cutoff':cutoff,'probabilities':p})
    report={'version':VERSION,'league':league,'created_at':as_of.isoformat(),'dataset_hash':dataset_hash,
            'source_results':len(rows),'scored_forecasts':len(forecasts),'skipped_insufficient_history':skipped,
            'mode':'retrospective latest-download replay','recommendation_eligible':False,
            'assumptions':['Results assumed available six hours after kickoff; this is not an observed publication time',
                           'Historical result/schedule corrections are not reconstructed',
                           'No historical bookmaker prices: no ROI, EV, CLV or profitability validation'],
            'markets':{},'reason':'Historical diagnostics alone never bypass live recommendation gates'}
    if len(forecasts)<120:
        report['reason']='At least 120 replayable forecasts are needed for a 60+ calibration sample and 40+ final test sample'
        return report
    # Hold out the latest 40% by date. All same-time fixtures remain in one split.
    boundary=forecasts[int(len(forecasts)*.6)]['cutoff']
    calibration=[f for f in forecasts if f['match'].kickoff+timedelta(hours=6)<boundary]
    test=[f for f in forecasts if f['cutoff']>=boundary]
    if len(calibration)<60 or len(test)<40:
        report['reason']='Insufficient distinct chronological calibration/test windows'
        return report
    report['split']={'calibration_count':len(calibration),'test_count':len(test),
                     'test_start':boundary.isoformat(),'test_end':test[-1]['cutoff'].isoformat(),
                     'calibration_last_kickoff':calibration[-1]['match'].kickoff.isoformat()}
    for market in forecasts[0]['probabilities']:
        xs=[f['probabilities'][market] for f in calibration]
        ys=[int(outcome(market,f['match'].home_goals,f['match'].away_goals)) for f in calibration]
        if len(set(ys))<2:continue
        cal=IsotonicRegression(out_of_bounds='clip').fit(xs,ys)
        raw=[f['probabilities'][market] for f in test]
        probabilities=cal.predict(raw).tolist()
        labels=[int(outcome(market,f['match'].home_goals,f['match'].away_goals)) for f in test]
        bins=[]
        for i in range(5):
            selected=[j for j,p in enumerate(probabilities) if i/5<=p<(i+1)/5 or i==4 and p==1]
            if selected:
                wins=sum(labels[j] for j in selected)
                bins.append({'low':i/5,'high':(i+1)/5,'n':len(selected),'observed':wins/len(selected),
                             'predicted':float(np.mean([probabilities[j] for j in selected])),
                             'interval':wilson(wins,len(selected))})
        score=metrics(probabilities,labels)
        benchmark=metrics([float(np.mean(ys))]*len(test),labels)
        ece=sum(b['n']*abs(b['predicted']-b['observed']) for b in bins)/len(test)
        report['markets'][market]={'raw':metrics(raw,labels),'calibrated':score,'frequency_baseline':benchmark,
                'ece':ece,'bins':bins,'diagnostic_pass':score['brier']<benchmark['brier'] and ece<=.08,
                'calibrator':{'x':cal.X_thresholds_.tolist(),'y':cal.y_thresholds_.tolist()}}
    return report
