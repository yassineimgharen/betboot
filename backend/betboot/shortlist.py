"""Research forecasts, explicitly separate from value-qualified recommendations."""
from datetime import datetime
import math
from zoneinfo import ZoneInfo
from betboot.domain import scope_matches

MIN_ODDS=1.50

MARKETS={'home','draw','away','1X','X2','12','over_1.5','under_1.5','over_2.5','under_2.5','over_3.5','under_3.5','btts_yes','btts_no'}


def in_scope(f,request,now,started):
    try:
        kickoff=datetime.fromisoformat(f['kickoff'])
        return (f['source'] in ('football-data.org','isportsapi.com') and scope_matches(f,request.leagues)
                and f['status']=='TIMED' and kickoff>now
                and kickoff.astimezone(ZoneInfo(request.timezone)).date()==request.day
                and started<=datetime.fromisoformat(f['collected_at'])<=now)
    except (KeyError,ValueError,TypeError):return False


def market_predictions(a,now,max_age):
    """One model leader per market family; alternatives, not a combined ticket."""
    families=(('1X2',('home','draw','away')),('Double Chance',('1X','X2','12')),
              ('Goals 1.5',('over_1.5','under_1.5')),('Goals 2.5',('over_2.5','under_2.5')),
              ('Goals 3.5',('over_3.5','under_3.5')),('BTTS',('btts_yes','btts_no')))
    forecasts=[]
    for family,keys in families:
        options=[]
        for key in keys:
            raw=a.get('ensemble',{}).get(key)
            if not isinstance(raw,(float,int)) or not math.isfinite(raw) or not 0<raw<=1:continue
            calibrated=[q for q in a.get('comparisons',[]) if q.get('selection')==key and q.get('calibrated')]
            probability=calibrated[0]['probability'] if calibrated else raw
            if not math.isfinite(probability) or not 0<probability<=1:continue
            quotes=[q for q in a.get('quotes',[]) if q['selection']==key and
                    0<=(now-datetime.fromisoformat(q['updated_at'])).total_seconds()<=max_age and
                    math.isfinite(q['odds']) and q['odds']>=MIN_ODDS]
            if not quotes:continue
            quote=max(quotes,key=lambda q:q['odds'])
            options.append({'market':family,'selection':key,'probability':probability,'raw_probability':raw,
                            'calibrated':bool(calibrated),'odds':quote.get('odds'),
                            'bookmaker':quote.get('bookmaker'),'updated_at':quote.get('updated_at')})
        # Rank using the same ensemble for both sides; calibration is disclosed separately.
        if options:forecasts.append(max(options,key=lambda p:p['raw_probability']))
    return forecasts


def research_candidates(analyses,request,now,started,max_age,min_probability):
    candidates=[]
    for a in analyses:
        f=a['fixture']
        if not in_scope(f,request,now,started) or not a.get('baseline') or not a.get('ensemble'):continue
        forecasts=market_predictions(a,now,max_age)
        options=[p for p in forecasts if p['probability']>=min_probability]
        if not options:continue
        leader=max(options,key=lambda p:(p['calibrated'],p['probability']))
        cross=a.get('reliability',{}).get('checks',{}).get('fixture_cross_checked',False) or any(
            0<=(now-datetime.fromisoformat(q['updated_at'])).total_seconds()<=max_age and
            (f['source']!='isportsapi.com' or q.get('source')=='The Odds API') for q in a.get('quotes',[]))
        gaps=list(a.get('missing',[]))
        if not cross:gaps.append('Fixture has one official source; independent corroboration unavailable')
        candidates.append({**leader,'fixture':f,'market_predictions':forecasts,
            'fixture_cross_checked':cross,'research_only':True,'eligible':False,'missing':gaps,
            'sources':a.get('sources',[]),'reason':'No verified statistical edge; '+
            ('calibrated model estimate' if leader['calibrated'] else 'model forecast not validated for this market')})
    return sorted(candidates,key=lambda p:(p['fixture_cross_checked'],p['calibrated'],p['probability']),reverse=True)


def take_research(candidates,picks,request,now,started,max_age):
    teams={(p['fixture']['source'],p['fixture'][side+'_id']) for p in picks for side in ('home','away')}
    result=[]
    for original in candidates:
        if len(result)+len(picks)>=request.count:break
        p=dict(original);f=p['fixture']
        pair={(f['source'],f['home_id']),(f['source'],f['away_id'])}
        if not in_scope(f,request,now,started) or pair&teams:continue
        if not isinstance(p.get('odds'),(float,int)) or not math.isfinite(p['odds']) or p['odds']<MIN_ODDS or not p.get('updated_at'):continue
        if not 0<=(now-datetime.fromisoformat(p['updated_at'])).total_seconds()<=max_age:
            continue
        forecasts=[]
        for original_forecast in p.get('market_predictions',[]):
            forecast=dict(original_forecast)
            if forecast.get('updated_at') and not 0<=(now-datetime.fromisoformat(forecast['updated_at'])).total_seconds()<=max_age:
                forecast.update(odds=None,bookmaker=None,updated_at=None)
            if forecast.get('odds') is not None and forecast['odds']>=MIN_ODDS:forecasts.append(forecast)
        p['market_predictions']=forecasts
        result.append(p);teams.update(pair)
    return result
