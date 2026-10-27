"""Separate xG model; fixed formula and chronological calibration, never baseline calibration."""
from datetime import UTC,datetime,timedelta
import numpy as np
from sklearn.isotonic import IsotonicRegression
from betboot.models.poisson import markets,InsufficientData
from betboot.sofascore_dataset import versions_as_of,timestamp,digest
from betboot.evaluation import metrics,wilson,calibrated_probability
from betboot.analytics import outcome
from betboot.identity import team_matches

VERSION='imported-xg-poisson-v1'


def features(rows,league,home,away,cutoff,target_id=None):
    history=sorted([r for r in versions_as_of(rows,cutoff) if r['league_id']==league and
        r['event_id']!=target_id and cutoff-timedelta(days=730)<=timestamp(r['kickoff'])<cutoff
        and r['home_xg'] is not None and r['away_xg'] is not None],key=lambda r:r['kickoff'])
    if len(history)<50:raise InsufficientData('Imported xG requires 50 earlier league matches and 5 venue matches per team')
    venue={'home':[r for r in history if r['home_id']==home][-20:],
           'away':[r for r in history if r['away_id']==away][-20:]}
    if min(map(len,venue.values()))<5:raise InsufficientData('Imported xG requires at least 5 earlier matches in each target venue')
    recent={side:[r for r in history if team in (r['home_id'],r['away_id'])][-10:] for side,team in [('home',home),('away',away)]}
    if any(cutoff-timestamp(v[-1]['kickoff'])>timedelta(days=60) for v in recent.values()):
        raise InsufficientData('Imported team xG history is older than 60 days')
    means={side:float(np.mean([r[side+'_xg'] for r in history])) for side in ('home','away')}
    if min(means.values())<=0:raise InsufficientData('Degenerate imported league xG distribution')
    def shrink(group,field,mean):return (sum(r[field] for r in group)+8*mean)/(len(group)+8)
    h=shrink(venue['home'],'home_xg',means['home'])*shrink(venue['away'],'home_xg',means['home'])/means['home']
    a=shrink(venue['away'],'away_xg',means['away'])*shrink(venue['home'],'away_xg',means['away'])/means['away']
    if max(h,a)>20:raise InsufficientData('Imported xG rates outside supported range')
    form={}
    for side,team in [('home',home),('away',away)]:
        values=recent[side]
        def average(field,opponent=False):
            return sum(r[('away' if r['home_id']==team else 'home')+'_'+field] if opponent else
                       r[('home' if r['home_id']==team else 'away')+'_'+field] for r in values)/len(values)
        form[side]={'matches':len(values),'xg_for':average('xg'),'xg_against':average('xg',True),
                    'goals_for':average('goals'),'goals_against':average('goals',True),
                    'last_result':values[-1]['kickoff'],'venue_matches':len(venue[side])}
    return {'version':VERSION,'league_id':league,'home_rate':h,'away_rate':a,'probabilities':markets(h,a),
            'form':form,'training_count':len(history),'training_ids':[r['event_id'] for r in history],
            'training_fingerprint':digest([r['fingerprint'] for r in history]),
            'formula':'Latest 20 venue xG means, shrunk by 8 league-average matches; attack × opponent defence / league mean',
            'sources':list(dict.fromkeys(u for r in history for u in r['source_urls']))}


def fingerprint(rows,league):
    return digest(sorted(r['fingerprint'] for r in rows if r['league_id']==league))


def validate(rows,league,now):
    latest=sorted([r for r in versions_as_of(rows,now) if r['league_id']==league],key=lambda r:r['kickoff'])
    predictions=[]
    for target in latest:
        cutoff=timestamp(target['kickoff'])-timedelta(hours=1)
        try:model=features(rows,league,target['home_id'],target['away_id'],cutoff,target['event_id'])
        except InsufficientData:continue
        predictions.append({'target':target,'cutoff':cutoff,'raw':model['probabilities']})
    report={'version':VERSION,'league_id':league,'league_name':latest[0]['league_name'] if latest else league,
            'as_of':now.isoformat(),'dataset_fingerprint':fingerprint(rows,league),'results':len(latest),
            'forecasts':len(predictions),'calibration_count':0,'test_count':0,'markets':{},
            'mode':'Chronological replay with recorded feature availability; not archived prospective forecasts',
            'reason':'Requires enough earlier xG records, 60 calibration examples, and 200 held-out tests for eligibility',
            'limitations':['Recorded kickoff times define replay targets; historical schedule changes are not reconstructed',
                          'Unknown capture times are not backdated; no historical odds or profit validation']}
    if len(predictions)<100:return report
    boundary=predictions[int(len(predictions)*.6)]['cutoff']
    train=[p for p in predictions if p['cutoff']<boundary and timestamp(p['target']['available_at'])<boundary]
    test=[p for p in predictions if p['cutoff']>=boundary]
    report.update(calibration_count=len(train),test_count=len(test),split_at=boundary.isoformat())
    if len(train)<60 or len(test)<40:return report
    for market in train[0]['raw']:
        labels=[int(outcome(market,p['target']['home_goals'],p['target']['away_goals'])) for p in train]
        if len(set(labels))<2:continue
        calibrator=IsotonicRegression(out_of_bounds='clip').fit([p['raw'][market] for p in train],labels)
        ps=calibrator.predict([p['raw'][market] for p in test]).tolist()
        ys=[int(outcome(market,p['target']['home_goals'],p['target']['away_goals'])) for p in test]
        score=metrics(ps,ys);benchmark=metrics([float(np.mean(labels))]*len(test),ys);bins=[]
        for i in range(5):
            indexes=[j for j,p in enumerate(ps) if i/5<=p<(i+1)/5 or i==4 and p==1]
            if indexes:
                bins.append({'low':i/5,'high':(i+1)/5,'n':len(indexes),
                             'predicted':float(np.mean([ps[j] for j in indexes])),
                             'observed':float(np.mean([ys[j] for j in indexes])),
                             'interval':wilson(sum(ys[j] for j in indexes),len(indexes))})
        ece=sum(b['n']*abs(b['predicted']-b['observed']) for b in bins)/len(test)
        report['markets'][f'{league}:{market}']={'eligible':len(test)>=200 and score['brier']<benchmark['brier'] and ece<=.08,
            'metrics':score,'frequency_baseline':benchmark,'ece':ece,'bins':bins,
            'calibrator':{'x':calibrator.X_thresholds_.tolist(),'y':calibrator.y_thresholds_.tolist()}}
    return report


def calibrated(report,rows,league,market,raw,now):
    if not report or report.get('version')!=VERSION or report.get('league_id')!=league:return None
    if not 0<=(now-timestamp(report['as_of'])).total_seconds()<=7*86400:return None
    if report.get('dataset_fingerprint')!=fingerprint(rows,league) or report.get('test_count',0)<200:return None
    result=calibrated_probability(report,league,market,raw)
    return result if result and result['calibration_bin_n']>=50 else None


def fixture_model(rows,fixture,now):
    known=versions_as_of(rows,now)
    # Only this core cross-provider competition identity has been verified locally.
    league='17' if fixture.league=='PL' else None
    if league is None:
        ids={r['league_id'] for r in known if r['country']==fixture.country and r['league_name']==fixture.league_name}
        if len(ids)==1:league=next(iter(ids))
    if league is None:return {'available':False,'reason':'No verified imported competition identity for this fixture'}
    group=[r for r in known if r['league_id']==league];teams={}
    for side in ('home','away'):
        ids=set()
        for r in group:
            for venue in ('home','away'):
                if team_matches(r[venue+'_name'],fixture,side,'the-odds-api'):
                    ids.add(r[venue+'_id'])
        if len(ids)!=1:return {'available':False,'reason':f'No unique imported {side} team identity'}
        teams[side]=next(iter(ids))
    try:
        model=features(rows,league,teams['home'],teams['away'],now)
        return {'available':True,**model}
    except InsufficientData as exc:return {'available':False,'league_id':league,'reason':str(exc)}
