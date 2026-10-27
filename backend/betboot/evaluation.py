"""Prospective walk-forward calibration from archived pre-match predictions only."""
import math
from datetime import datetime

import numpy as np
from sklearn.isotonic import IsotonicRegression

from betboot.analytics import VERSION, outcome


def wilson(wins, n):
    if not n:
        return [0., 1.]
    z = 1.95996398454
    p = wins/n
    mid = (p+z*z/(2*n))/(1+z*z/n)
    radius = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return [max(0.,mid-radius), min(1.,mid+radius)]


def metrics(probabilities, outcomes):
    p, y = np.clip(probabilities, 1e-9, 1-1e-9), np.asarray(outcomes)
    return {'n':len(y), 'brier':float(np.mean((p-y)**2)),
            'log_loss':float(np.mean(-y*np.log(p)-(1-y)*np.log(1-p)))}


def evaluate(predictions, results, cutoff):
    """Separate train(calibrator), then chronological OOS predictions. No backdated downloads.

    Only one forecast per fixture, league and version; calibration gets results known
    BEFORE the earliest test prediction. Every test fold is an expanding window.
    """
    def results_at(t):
        versions={}
        for m in sorted(results,key=lambda m:m.collected_at):
            if m.collected_at<=t:
                versions[(m.source,m.external_id)]=m
        return {k:m for k,m in versions.items() if m.status=='FINISHED'}
    latest = results_at(cutoff)
    unique = {}
    for p in sorted(predictions, key=lambda p:p['as_of']):
        if p.get('analysis_version') != VERSION or 'ensemble' not in p:
            continue
        f = p['fixture']; key=(f['source'],f['external_id'])
        t = datetime.fromisoformat(p['as_of'])
        if t >= datetime.fromisoformat(f['kickoff']) or t>cutoff:
            continue
        if key in latest and latest[key].kickoff > t:
            unique.setdefault(key,p)
    samples = sorted(unique.values(), key=lambda p:p['as_of'])
    report = {'version':VERSION,'as_of':cutoff.isoformat(),'sample_count':len(samples),'markets':{},
              'mode':'prospective archived predictions', 'note':'No profitability claim; calibration intervals describe observed bin frequencies'}
    for league in sorted({p['fixture']['league'] for p in samples}):
        group = [p for p in samples if p['fixture']['league']==league]
        for market in (group[0]['ensemble'] if group else {}):
            oos=[]
            for test in group:
                t = datetime.fromisoformat(test['as_of'])
                known = results_at(t)
                train = [p for p in group if p['as_of']<test['as_of'] and
                         (p['fixture']['source'],p['fixture']['external_id']) in known and
                         known[(p['fixture']['source'],p['fixture']['external_id'])].collected_at<t]
                if len(train)<60:
                    continue
                xs=[p['ensemble'][market] for p in train]
                ys=[int(outcome(market, known[(p['fixture']['source'],p['fixture']['external_id'])].home_goals,
                                known[(p['fixture']['source'],p['fixture']['external_id'])].away_goals)) for p in train]
                if len(set(ys))<2:
                    continue
                cal=IsotonicRegression(out_of_bounds='clip').fit(xs,ys)
                result=latest[(test['fixture']['source'],test['fixture']['external_id'])]
                oos.append((float(cal.predict([test['ensemble'][market]])[0]),
                            int(outcome(market,result.home_goals,result.away_goals)), float(np.mean(ys))))
            if len(oos)<40:
                report['markets'][f'{league}:{market}']={'eligible':False,'reason':'Requires 60 calibration and 40 later scored forecasts','oos_count':len(oos)}
                continue
            ps,ys,base=map(list,zip(*oos))
            bins=[]
            for i in range(5):
                idx=[j for j,p in enumerate(ps) if i/5<=p<(i+1)/5 or (i==4 and p==1)]
                if idx:
                    bins.append({'low':i/5,'high':(i+1)/5,'n':len(idx),
                                 'predicted':float(np.mean([ps[j] for j in idx])),
                                 'observed':float(np.mean([ys[j] for j in idx])),
                                 'interval':wilson(sum(ys[j] for j in idx),len(idx))})
            score=metrics(ps,ys); benchmark=metrics(base,ys)
            ece=sum(b['n']*abs(b['predicted']-b['observed']) for b in bins)/len(ys)
            xs=[p['ensemble'][market] for p in group]
            labels=[int(outcome(market,latest[(p['fixture']['source'],p['fixture']['external_id'])].home_goals,
                                latest[(p['fixture']['source'],p['fixture']['external_id'])].away_goals)) for p in group]
            cal=IsotonicRegression(out_of_bounds='clip').fit(xs,labels)
            report['markets'][f'{league}:{market}']={'eligible':score['brier']<benchmark['brier'] and ece<=.08,
                'metrics':score,'frequency_baseline':benchmark,'ece':ece,'bins':bins,
                'calibrator':{'x':cal.X_thresholds_.tolist(),'y':cal.y_thresholds_.tolist()},
                'reason':'Requires better OOS Brier than expanding frequency baseline and ECE <= 0.08'}
    return report


def calibrated_probability(report, league, market, raw):
    m=report.get('markets',{}).get(f'{league}:{market}',{})
    if not m.get('eligible'):
        return None
    p=float(np.interp(raw,m['calibrator']['x'],m['calibrator']['y']))
    b=next((b for b in m['bins'] if b['low']<=p<b['high'] or p==1 and b['high']==1),None)
    if not b or b['n']<20:
        return None
    return {'probability':p,'interval':b['interval'],'interval_method':'95% Wilson interval for OOS calibration-bin frequency',
            'calibration_bin_n':b['n']}
