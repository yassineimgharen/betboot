import asyncio
import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy.orm import Session

from betboot.analytics import VERSION, advanced_models, handicap_distribution, handicap_value, outcome, score_grid
from betboot.collection import collect
from betboot.db import Observation, Prediction, as_of_matches
from betboot.domain import LEAGUES, COUNTRIES, MatchObservation, scope_matches
from betboot.providers.world import discover, CORE_WORLD_CODES, bootstrap_history as bootstrap_world_history
from betboot.evaluation import calibrated_probability, evaluate
from betboot.features import team_form
from betboot.models.poisson import InsufficientData, predict
from betboot.providers.base import ProviderError
from betboot.providers.football_data import FootballDataProvider
from betboot.research import ResearchProviders, official_feeds
from betboot.store import read, save
from betboot.sofascore_dataset import records as imported_records
from betboot.models.imported_xg import fixture_model as imported_fixture_model, calibrated as imported_calibrated
from betboot.backtest import historical_probability, historical_validation, replay_probabilities


def window(day=None,hours=24,timezone='America/New_York',now=None):
    now=now or datetime.now(UTC)
    if hours not in (24,48,72):
        raise ValueError('Window must be 24, 48 or 72 hours')
    try:
        tz=ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        raise ValueError('Unknown timezone') from None
    if day:
        d=date.fromisoformat(day)
        start=datetime.combine(d,time.min,tzinfo=tz)
        end=datetime.combine(d+timedelta(days=1),time.min,tzinfo=tz)
        return start.astimezone(UTC),end.astimezone(UTC)
    return now,now+timedelta(hours=hours)


def model_report(engine,now):
    with Session(engine) as db:
        forecasts=[p.payload for p in db.query(Prediction).order_by(Prediction.created_at).all()]
        results=[MatchObservation.model_validate(o.payload) for o in db.query(Observation).filter(Observation.collected_at<=now).all()]
    return evaluate(forecasts,results,now)


def research_analysis(fixture,history,now,quotes,evidence,cross_checked,missing,validation,max_age=600,historical=None,min_probability=0,xg_rows=None,xg_reports=None,prefer_high_probability=False,min_odds=1.0):
    quotes=[q for q in quotes if fixture.kickoff>now and
            0<=(now-datetime.fromisoformat(q['updated_at'])).total_seconds()<=max_age]
    sources=[{'kind':'fixture_and_results','source':fixture.source,
              'url':(f'https://api.isportsapi.com/sport/football/schedule/basic?matchId={fixture.external_id}' if fixture.source=='isportsapi.com' else f'https://api.football-data.org/v4/matches/{fixture.external_id}'),
              'collected_at':fixture.collected_at.isoformat(),'source_updated_at':fixture.source_updated_at.isoformat() if fixture.source_updated_at else None,
              'verification':'Provider observation; schedules may be delayed'}]+evidence
    report={'fixture':fixture.model_dump(mode='json'),'as_of':now.isoformat(),'analysis_version':VERSION,
            'status':'NO BET','min_probability':min_probability,'reasons':[],'sources':sources,'quotes':quotes,'comparisons':[],
            'recommendations':[],'missing':list(missing),'models':{},'ensemble':{},
            'form':{side:team_form(history,fixture.source,getattr(fixture,f'{side}_id'),now) for side in ('home','away')}}
    xg_rows=xg_rows or []
    xg=imported_fixture_model(xg_rows,fixture,now) if xg_rows else {'available':False,'reason':'No imported xG dataset'}
    report['imported_xg']=xg
    xg_report=next((r for r in (xg_reports or []) if r.get('league_id')==xg.get('league_id')),None)
    if xg.get('available'):
        sources.append({'kind':'historical_xg','source':'Imported Sofascore JSON','url':xg['sources'][0],
                        'collected_at':now.isoformat(),'verification':'Derived at analysis time from imported records; capture/import availability enforced',
                        'data':{'training_fingerprint':xg['training_fingerprint'],'training_count':xg['training_count'],
                                'training_ids':xg['training_ids'],'source_urls':xg['sources']}})
    report['rest_and_congestion']={}
    for side in ('home','away'):
        team=getattr(fixture,f'{side}_id')
        prior=[m for m in history if m.source==fixture.source and m.status=='FINISHED' and
               team in (m.home_id,m.away_id) and m.kickoff<now and m.collected_at<=now]
        report['rest_and_congestion'][side]={
            'days_since_last_collected_match':(fixture.kickoff-max(m.kickoff for m in prior)).total_seconds()/86400 if prior else None,
            'matches_in_prior_14_days':sum(m.kickoff>=fixture.kickoff-timedelta(days=14) for m in prior),
            'coverage_note':'Collected competitions only; domestic cups and international fixtures may be missing'}
    try:
        baseline=predict(fixture,history,now)
    except InsufficientData as exc:
        report['reasons'].append(str(exc)); report['reliability']={'score':0,'label':'Insufficient model inputs, not a win probability'}
        return report
    advanced=advanced_models(history,fixture,baseline,now)
    report.update({'baseline':baseline,'models':advanced['models'],'ensemble':advanced['ensemble'],
                   'model_notes':advanced['unavailable'],'rho':advanced['rho']})
    if xg.get('available'):
        report['models']['imported_xg_poisson']=xg['probabilities']
    kinds={s['kind'] for s in evidence}
    checks={'fresh_fixture':(now-fixture.collected_at).total_seconds()<=86400,
            'history_sufficient':True,'fixture_cross_checked':cross_checked,
            'injury_reports_available':'injuries' in kinds,'lineups_available':'lineups' in kinds,
            'fresh_odds_available':bool(quotes)}
    weights={'fresh_fixture':15,'history_sufficient':25,'fixture_cross_checked':20,
             'injury_reports_available':10,'lineups_available':10,'fresh_odds_available':20}
    reliability=sum(weights[k] for k,v in checks.items() if v)
    report['reliability']={'score':reliability,'checks':checks,'weights':weights,
        'label':'Input completeness rubric v1, not model confidence or a probability of winning'}
    report['missing'] += [k for k,v in checks.items() if not v]
    report['missing'] += ([] if xg.get('available') else ['Shot-based xG/xGA'])+['Independent injury/lineup corroboration','Verified tactical changes']
    candidates=[]
    historical_raw=replay_probabilities(fixture,history,now) if historical else None
    for quote in quotes:
        key=quote['selection']; odds=quote['odds']
        comparison={**quote,'eligible':False}
        if key.startswith('ah_'):
            _,side,line=key.split('_')
            distribution=handicap_distribution(score_grid(baseline['home_goal_rate'],baseline['away_goal_rate'],advanced['rho']),float(line),side)
            comparison.update(handicap_value(distribution,odds))
            comparison['reason']='Asian-handicap settlement distribution is uncalibrated; research only'
        elif key in advanced['ensemble']:
            raw=advanced['ensemble'][key]
            calibrated=calibrated_probability(validation,fixture.league,key,raw)
            validation_mode='prospective'
            if calibrated is None and historical_raw and historical.get('league')==fixture.league:
                calibrated=historical_probability(historical,key,historical_raw[key],now)
                if calibrated:
                    raw=historical_raw[key]
                    validation_mode='historical_holdout'
            if calibrated is None and xg.get('available') and key in xg['probabilities']:
                candidate_raw=xg['probabilities'][key]
                calibrated=imported_calibrated(xg_report,xg_rows,xg['league_id'],key,candidate_raw,now)
                if calibrated:
                    raw=candidate_raw
                    validation_mode='imported_xg_chronological_holdout'
            comparison.update({'raw_probability':raw,'probability':raw,'fair_odds':1/raw if raw else None,
                               'expected_value':raw*odds-1,'calibrated':False,'confidence_interval':None})
            if calibrated:
                p=calibrated['probability']; low,high=calibrated['interval']
                comparison.update({'probability':p,'fair_odds':1/p if p else None,'expected_value':p*odds-1,
                    'calibrated':True,'confidence_interval':[low,high],'interval_method':calibrated['interval_method'],
                    'calibration_bin_n':calibrated['calibration_bin_n'],'validation_mode':validation_mode,
                    'raw_model':('imported xG Poisson' if validation_mode=='imported_xg_chronological_holdout' else 'poisson replay' if validation_mode=='historical_holdout' else 'ensemble')})
                # Lower confidence bound must imply an edge; avoid large uncertainty and missing context.
                if odds>=min_odds and p>=min_probability and p*odds-1>=.02 and low*odds-1>=.02 and high-low<=.35 and all(checks.values()):
                    comparison['eligible']=True
                    candidates.append({**comparison,'risk_level':'Conservative' if p>=.65 and high-low<=.2 else 'Balanced' if p>=.45 else 'Aggressive',
                        'supporting_evidence':([xg['formula']] if validation_mode=='imported_xg_chronological_holdout' else baseline['reasons']),'sources':sources,'last_research_update':now.isoformat(),
                        'reliability_score':reliability,'fixture':fixture.model_dump(mode='json'),
                        'warning':('Historical held-out calibration, not prospective or profit validation. ' if validation_mode!='prospective' else '')+
                                  'Estimated edge; context reports have only one provider. Verify updates before any manual decision.'})
            else:
                comparison['reason']='No sufficient passing prospective or held-out historical calibration for this league/market/bin'
        report['comparisons'].append(comparison)
    # One opportunity per fixture prevents multiple highly correlated selections.
    if candidates:
        report['recommendations']=[max(candidates,key=lambda c:(c['probability'],c['confidence_interval'][0]*c['odds']-1) if prefer_high_probability else (c['confidence_interval'][0]*c['odds']-1,))]
        report['status']='REVIEW'
    else:
        report['reasons'].append('No opportunity passes calibration, uncertainty, freshness and context gates')
    return report


async def scan(engine,settings,day=None,hours=24,timezone='America/New_York',match_id=None,deep=False,min_probability=0,country=None,match_source='football-data.org',selected_leagues=None,bootstrap_history=False,prefer_high_probability=False,min_odds=1.0,defer_odds=False):
    started=datetime.now(UTC); start,end=window(day,hours,timezone,started)
    selectors=('*',) if selected_leagues is None else tuple(selected_leagues)
    if not selectors or any(s not in LEAGUES and s!='*' and not s.startswith('COUNTRY:') for s in selectors):raise ValueError('Unsupported selection')
    countries=tuple(s[8:] for s in selectors if s.startswith('COUNTRY:'))
    chosen=tuple(l for l in LEAGUES if scope_matches({'league':l},selectors))
    global_scope='*' in selectors or bool(countries)
    errors=[]; runs=[]
    async with httpx.AsyncClient(follow_redirects=False) as client:
        if settings.football_data_token.get_secret_value():
            provider=FootballDataProvider(client,settings.football_data_token.get_secret_value(),settings.provider_request_interval)
            seasons=sorted({(d.year if d.month>=7 else d.year-1) for d in (start,end)})
            # Collect current season; prior seasons are explicit CLI imports to control entitlements/quota.
            for league in chosen:
                for season in seasons:
                    try:
                        runs.append(await collect(engine,provider,league,season))
                    except ProviderError as exc:
                        errors.append(f'{league}: {exc}')
                if bootstrap_history:
                    with Session(engine) as db:
                        known=as_of_matches(db,datetime.now(UTC),league)
                    for old_season in range(min(seasons)-1,min(seasons)-settings.history_seasons-1,-1):
                        if sum(m.source=='football-data.org' and m.season==old_season and m.status=='FINISHED' for m in known)>=100:
                            continue
                        try:runs.append(await collect(engine,provider,league,old_season))
                        except ProviderError as exc:errors.append(f'{league} history {old_season}: {exc}')

        else:
            errors.append('FOOTBALL_DATA_TOKEN is not configured; using only previously collected observations')
        providers=ResearchProviders(client,settings)
        if global_scope and not settings.isports_api_key.get_secret_value():errors.append('ISPORTS_API_KEY is required for country/worldwide discovery')
        if global_scope and settings.isports_api_key.get_secret_value() and not match_id:
            try:
                world_runs,rejected=await discover(engine,providers.isports,start,end,country or (countries if '*' not in selectors else None))
                runs.extend(world_runs)
                if rejected: errors.append(f'Global discovery rejected {rejected} invalid or unsupported records')
                if bootstrap_history:
                    history_runs,history_errors=await bootstrap_world_history(engine,providers.isports,start,end,selectors,settings.world_history_leagues)
                    runs.extend(history_runs);errors.extend(history_errors)
            except ProviderError as exc:
                errors.append(f'Global discovery: {exc}')
        now=datetime.now(UTC)
        with Session(engine) as db:
            history=as_of_matches(db,now)
        fixtures=[m for m in history if scope_matches(m.model_dump(),selectors) and m.league not in CORE_WORLD_CODES and m.status=='TIMED' and m.kickoff>now and
                  ((m.external_id==match_id and m.source==match_source) if match_id else start<=m.kickoff<end) and (not country or (m.country or COUNTRIES.get(m.league))==country)]
        validation=await asyncio.to_thread(model_report,engine,now)
        save(engine,'evaluation',validation)
        historical_reports={}
        for league in {m.league for m in fixtures}:
            historical_reports[league]=await asyncio.to_thread(historical_validation,history,now,league)
            save(engine,'backtest',historical_reports[league])
        xg_rows=imported_records(engine)
        xg_reports=read(engine,'sofa_validation',100)
        analyses=[]
        odds_paused=False; context_paused=False
        for fixture in sorted(fixtures,key=lambda m:m.kickoff):
            missing=[]; quotes=[]; evidence=[]; cross=False
            if fixture.source=='isportsapi.com':
                try:
                    predict(fixture,history,datetime.now(UTC))
                except InsufficientData:
                    # Discovery can return thousands of fixtures. Do not spend odds
                    # quota on leagues that cannot yet support a baseline forecast.
                    analysis=research_analysis(fixture,history,datetime.now(UTC),[],[],False,
                        ['Global fixture discovered; sufficient league/team history is not yet collected. '
                         'Deep Analyze Match can retrieve context, but cannot replace missing history.'],
                        validation,min_probability=min_probability,xg_rows=xg_rows,xg_reports=xg_reports)
                    analysis['id']=save(engine,'analysis',analysis)
                    analyses.append(analysis)
                    continue
            try:
                quotes,reason=([], 'Odds deferred until context research completes') if defer_odds else ([], 'Odds research paused after quota exhaustion') if odds_paused else await providers.odds(fixture)
                if reason: missing.append(reason)
            except ProviderError as exc:
                missing.append(str(exc))
                odds_paused='quota exhausted' in str(exc)
            try:
                evidence,cross,context_missing=([],False,['Context research paused after quota exhaustion']) if context_paused else await providers.context(fixture,deep)
                missing.extend(context_missing)
                if fixture.source=='isportsapi.com' and any(q['source']=='The Odds API' for q in quotes):
                    cross=True
                    evidence.append({'kind':'fixture_cross_check','source':'The Odds API','url':next(q['url'] for q in quotes if q['source']=='The Odds API'),
                                     'collected_at':quotes[0]['collected_at'],'verification':'Independent exact team and kickoff agreement in odds feed'})
            except (ProviderError,KeyError,TypeError,ValueError) as exc:
                missing.append(str(exc) if isinstance(exc,ProviderError) else 'Context provider schema validation failed')
                context_paused=isinstance(exc,ProviderError) and 'quota exhausted' in str(exc)
            if deep and settings.official_feed_urls:
                feed_evidence,feed_gaps=await official_feeds(client,settings.official_feed_urls,fixture)
                evidence.extend(feed_evidence);missing.extend(feed_gaps)
            analysis=await asyncio.to_thread(research_analysis,fixture,history,datetime.now(UTC),quotes,evidence,cross,missing,validation,settings.odds_max_age_seconds,historical_reports.get(fixture.league),min_probability,xg_rows,xg_reports,prefer_high_probability,min_odds)
            identifier=save(engine,'analysis',analysis); analysis['id']=identifier
            if analysis['ensemble']:
                with Session(engine) as db,db.begin():
                    db.add(Prediction(id=identifier,source=fixture.source,external_id=fixture.external_id,
                        created_at=datetime.fromisoformat(analysis['as_of']),model_version=VERSION,payload=analysis))
            analyses.append(analysis)
        # Avoid multiple opportunities involving a team in the same scan.
        ranked=sorted([r for a in analyses for r in a['recommendations']],key=lambda r:r['expected_value'],reverse=True)
        used=set(); recommendations=[]
        for r in ranked:
            f=r['fixture']; keys={(f['source'],f['home_id']),(f['source'],f['away_id'])}
            if not used.intersection(keys):
                recommendations.append(r); used.update(keys)
        result={'started_at':started.isoformat(),'finished_at':datetime.now(UTC).isoformat(),
                'window':{'start':start.isoformat(),'end':end.isoformat(),'timezone':timezone},
                'status':'complete_with_gaps' if errors else 'complete','min_probability':min_probability,'errors':errors,'collection_runs':runs,
                'analyses':analyses,'recommendations':recommendations,'mode':'research_only',
                'summary':f'{len(analyses)} matches researched; {len(recommendations)} opportunities for manual review'}
        result['id']=save(engine,'scan',result)
        return result


def paper_results(engine):
    """One hypothetical unit per archived recommendation. No bankroll or stake advice."""
    scans=read(engine,'scan',1000); selections={}
    for scan_row in reversed(scans):
        for r in scan_row.get('recommendations',[]):
            f=r['fixture']; key=(f['source'],f['external_id'])
            selections.setdefault(key,r)
    with Session(engine) as db:
        results={(m.source,m.external_id):m for m in as_of_matches(db,datetime.now(UTC))}
    bets=[]; profit=0.; peak=0.; drawdown=0.; settled=0; wins=0; equity=[]
    ordered=sorted(selections.items(),key=lambda item:item[1]['fixture']['kickoff'])
    for key,r in ordered:
        match=results.get(key); pnl=None; state='pending'
        if match and match.status=='FINISHED':
            won=outcome(r['selection'],match.home_goals,match.away_goals)
            pnl=r['odds']-1 if won else -1.; state='won' if won else 'lost'
            settled+=1; wins+=int(won)
        elif match and match.status in ('CANCELLED','AWARDED'):
            pnl=0.;state='void'
        if pnl is not None:
            profit+=pnl;peak=max(peak,profit);drawdown=max(drawdown,peak-profit)
            equity.append({'kickoff':r['fixture']['kickoff'],'profit_units':profit})
        bets.append({'fixture':r['fixture'],'selection':r['selection'],'odds':r['odds'],'status':state,'profit_units':pnl})
    return {'mode':'hypothetical one-unit observations; no money or stake instructions',
            'bets':bets,'settled':settled,'profit_units':profit,'yield':profit/settled if settled else None,
            'win_rate':wins/settled if settled else None,'max_drawdown_units':drawdown,'equity':equity,
            'clv':None,'clv_reason':'No verified same-market closing snapshot has been identified'}
