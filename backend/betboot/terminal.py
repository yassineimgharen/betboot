"""Three questions, autonomous research, one compact manual betting slip."""
import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC,date,datetime,timedelta
import fcntl
import json
import logging
import math
from pathlib import Path
import sys
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session
from betboot.config import Settings
from betboot.db import Base,engine_for,as_of_matches
from betboot.domain import LEAGUES,MatchObservation,scope_matches
from betboot.pipeline import scan,window,research_analysis,model_report
from betboot.providers.football_data import FootballDataProvider
from betboot.providers.base import ProviderError
from betboot.research import ResearchProviders
from betboot.sofascore_dataset import records
from betboot.store import read,save
from betboot.shortlist import research_candidates,take_research,market_predictions,MIN_ODDS
from betboot.gemini import rank_candidates

NAMES={'PL':'Premier League','PD':'La Liga','SA':'Serie A','BL1':'Bundesliga','FL1':'Ligue 1'}
MARKETS={'home','draw','away','1X','X2','12','over_1.5','under_1.5','over_2.5','under_2.5','over_3.5','under_3.5','btts_yes','btts_no'}

@dataclass(frozen=True)
class Request:
    count:int
    leagues:tuple[str,...]
    day:date
    timezone:str


def parse_leagues(value):
    if value.strip().casefold()=='all':return ('*',)
    if value.strip().casefold()=='top5':return tuple(NAMES)
    aliases={**{k.casefold():k for k in NAMES},**{v.casefold():k for k,v in NAMES.items()},'epl':'PL','english premier league':'PL'}
    parts=[p.strip().casefold() for p in value.split(',')]
    if not parts or any(not p or not all(c.isalpha() or c in " -&'" for c in p) for p in parts if p not in aliases):
        raise ValueError('Choose league names or country names separated by commas, TOP5, or ALL.')
    return tuple(dict.fromkeys(aliases[p] if p in aliases else 'COUNTRY:'+p for p in parts))


def ask(timezone,input_fn=input,now=None):
    today=(now or datetime.now(UTC)).astimezone(ZoneInfo(timezone)).date()
    count_text=input_fn('How many matches do you want in your betting slip? ').strip()
    if not count_text.isdigit() or not 1<=int(count_text)<=20:raise ValueError('Match count must be between 1 and 20.')
    leagues=parse_leagues(input_fn('Which leagues/countries? e.g. Premier League, England, Spain, Brazil; TOP5 or ALL (worldwide): '))
    chosen=input_fn(f'Which date? Today, Tomorrow, or YYYY-MM-DD ({timezone}): ').strip()
    day=today if chosen.casefold()=='today' else today+timedelta(days=1) if chosen.casefold()=='tomorrow' else date.fromisoformat(chosen)
    if day<today:raise ValueError('Choose today or a future date; started matches are excluded.')
    return Request(int(count_text),leagues,day,timezone)


@contextmanager
def exclusive_run(path=Path('.betboot.lock')):
    with path.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another Betboot run is active. Let it finish before starting another.') from None
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def select_picks(candidates,request,now,max_age,started_at,min_probability=0.65):
    selected=[];matches=set();teams=set()
    for pick in sorted(candidates,key=lambda p:(p.get('probability',-1),p.get('expected_value',-1)),reverse=True):
        try:
            f=pick['fixture'];kickoff=datetime.fromisoformat(f['kickoff']);updated=datetime.fromisoformat(pick['updated_at'])
            age=(now-updated).total_seconds();prob=pick['probability'];odds=pick['odds'];interval=pick['confidence_interval']
            if (not scope_matches(f,request.leagues) or kickoff<=now or kickoff.astimezone(ZoneInfo(request.timezone)).date()!=request.day
                or f['status']!='TIMED' or not started_at<=datetime.fromisoformat(f['collected_at'])<=now
                or pick['selection'] not in MARKETS or not pick.get('eligible') or not pick.get('calibrated')
                or not 0<=age<=max_age or not math.isfinite(prob) or not min_probability<=prob<=1
                or not math.isfinite(odds) or odds<MIN_ODDS or prob*odds-1<.02
                or len(interval)!=2 or not 0<=interval[0]<=interval[1]<=1
                or interval[0]*odds-1<.02 or interval[1]-interval[0]>.35):continue
            key=(f['source'],f['external_id']);pair={(f['source'],f['home_id']),(f['source'],f['away_id'])}
            if key in matches or teams.intersection(pair):continue
            selected.append(pick);matches.add(key);teams.update(pair)
            if len(selected)==request.count:break
        except (KeyError,TypeError,ValueError,OverflowError):continue
    return selected


def selection(pick):
    f=pick['fixture'];key=pick['selection']
    labels={'home':f"{f['home_name']} — W1 (Home Win)",'draw':'X (Draw)',
            'away':f"{f['away_name']} — W2 (Away Win)",
            '1X':f"{f['home_name']} — 1X (Home or Draw)",'X2':f"{f['away_name']} — X2 (Away or Draw)",
            '12':'12 (Either Team Wins)','btts_yes':'Both Teams To Score — Yes','btts_no':'Both Teams To Score — No'}
    return labels.get(key,key.replace('_',' ').title()+' Goals')


def render(picks,request,reason=None,research=None,coverage=None,details=False):
    research=research or []
    if not picks and not research:
        message=f'SLIP UNAVAILABLE — 0/{request.count} VERIFIED SELECTIONS\n'+(reason or 'No selections passed the current data, calibration and value checks.')
        if coverage:
            message+=f"\nRequested {request.count}; researched {coverage['researched']} unstarted fixtures in the selected leagues/date."
            if coverage['researched']<request.count:message+=' Choose ALL for worldwide discovery or another date for more fixtures.'
        return message
    lines=['⚽ MATCH SHORTLIST' if research else '⚽ MY BETTING SLIP','']
    if research:lines.append('Model predictions, not known results. Research-only entries are not qualified bets.\n')
    for i,p in enumerate(picks+research,1):
        f=p['fixture'];kickoff=datetime.fromisoformat(f['kickoff']).astimezone(ZoneInfo(request.timezone))
        label='Model lean' if p.get('research_only') else 'Value-qualified selection'
        detail=f"{kickoff:%Y-%m-%d %H:%M %Z}"
        if p.get('calibrated'):detail+=f" · Estimate {p['probability']:.0%}"
        else:detail+=' · Unvalidated forecast; no reliable probability available'
        if p.get('odds'):detail+=f" · Odds {p['odds']:.2f} ({p['bookmaker']})"
        else:detail+=' · Verified odds unavailable'
        lines.extend([f"{i}. {f['home_name']} vs {f['away_name']}",f"   {label}: {selection(p)}",f"   {detail}"])
        if p.get('research_only'):lines.append('   Research only — no verified edge; context may be incomplete.')
        if p.get('fixture_cross_checked') is False:lines.append('   Official fixture verified; independent corroboration unavailable.')
        if details and p.get('market_predictions'):
            lines.append('   Market alternatives (do not combine as independent bets):')
            for forecast in p['market_predictions']:
                text=selection({**forecast,'fixture':f})
                estimate=f"{forecast['probability']:.0%} calibrated estimate" if forecast['calibrated'] else 'unvalidated model forecast'
                price=f"; odds {forecast['odds']:.2f} ({forecast['bookmaker']})" if forecast.get('odds') else '; odds unavailable'
                lines.append(f"   • {forecast['market']}: {text} — {estimate}{price}")
    total=len(picks)+len(research)
    if total<request.count:lines.append(f'\nOnly {total} of {request.count} requested matches met the requirements.')
    if total<request.count and coverage:
        lines.append(f"Researched {coverage['researched']} fixtures in your selected leagues/date; "
                     f"{coverage['with_history']} had usable model history. Started or unverifiable fixtures are excluded.")
        lines.append('Choose ALL or another date to search more matches.')
    # Do not present a product of prices from different books as an executable ticket quote.
    lines.append('\nVerify these prices in 1xBet. Combining matches increases the chance the slip loses.')
    return '\n'.join(lines)


async def generate(request,settings,engine):
    started=datetime.now(UTC)
    if not settings.football_data_token.get_secret_value() and not settings.isports_api_key.get_secret_value():
        return {'picks':[],'reason':'FOOTBALL_DATA_TOKEN or ISPORTS_API_KEY is required in .env.','errors':[],'analyses':[]}
    settings=settings.model_copy(update={'odds_extra_markets':True})
    initial=await scan(engine,settings,day=request.day.isoformat(),timezone=request.timezone,
                       selected_leagues=request.leagues,bootstrap_history=True,
                       deep=not any(s=='*' or s.startswith('COUNTRY:') for s in request.leagues),defer_odds=True,
                       min_probability=settings.slip_min_probability,prefer_high_probability=True,min_odds=MIN_ODDS)
    now=datetime.now(UTC)
    with Session(engine) as db:history=as_of_matches(db,now)
    historical={}
    for report in read(engine,'backtest',100):historical.setdefault(report['league'],report)
    validation=model_report(engine,now);xg_rows=records(engine);xg_reports=read(engine,'sofa_validation',100)
    final=[];errors=list(initial['errors']);checks=[];odds_paused=False
    async with httpx.AsyncClient(follow_redirects=False) as client:
        providers=ResearchProviders(client,settings)
        # Refresh odds AFTER potentially slow context research, then recalculate EV.
        for original in initial['analyses']:
            fixture=MatchObservation.model_validate(original['fixture'])
            if not original['ensemble']:final.append(original);continue
            evidence=[s for s in original['sources'] if s['kind'] not in ('fixture_and_results','historical_xg')
                      and 0<=(datetime.now(UTC)-datetime.fromisoformat(s['collected_at'])).total_seconds()<=900]
            cross=original['reliability'].get('checks',{}).get('fixture_cross_checked',False) and any(s['kind']=='fixture' for s in evidence)
            quotes=[];gaps=[]
            try:
                quotes,gap=([], 'Odds provider quota exhausted') if odds_paused else await providers.odds(fixture)
                if gap:gaps.append(gap)
            except ProviderError as exc:
                gaps.append(str(exc));odds_paused='quota exhausted' in str(exc)
            final.append(research_analysis(fixture,history,datetime.now(UTC),quotes,evidence,cross,gaps,validation,
                settings.odds_max_age_seconds,historical.get(fixture.league),xg_rows=xg_rows,xg_reports=xg_reports,
                min_probability=settings.slip_min_probability,prefer_high_probability=True,min_odds=MIN_ODDS))
        candidates=[p for a in final for p in a['recommendations']]
        all_candidates=Request(max(1,len(candidates)),request.leagues,request.day,request.timezone)
        proposed=select_picks(candidates,all_candidates,datetime.now(UTC),settings.odds_max_age_seconds,started,settings.slip_min_probability)
        fallback=research_candidates(final,request,datetime.now(UTC),started,
                                     settings.odds_max_age_seconds,settings.slip_min_probability)
        fallback_request=Request(max(1,len(fallback)+len(proposed)),request.leagues,request.day,request.timezone)
        fallback=take_research(fallback,proposed,fallback_request,datetime.now(UTC),started,settings.odds_max_age_seconds)
        fallback,gemini=await rank_candidates(client,settings,fallback)
        verified=[];verified_research=[]
        provider=FootballDataProvider(client,settings.football_data_token.get_secret_value(),settings.provider_request_interval) if settings.football_data_token.get_secret_value() else None
        world_candidates=[p for p in proposed+fallback if p['fixture']['source']=='isportsapi.com']
        verification_batches={}
        for p in proposed+fallback:
            f=p['fixture']
            try:
                if f['source']=='isportsapi.com':
                    from betboot.providers.isports import agrees
                    batch=world_candidates.index(p)//100
                    if batch not in verification_batches:
                        ids=','.join(item['fixture']['external_id'] for item in world_candidates[batch*100:(batch+1)*100])
                        verification_batches[batch]=await providers.isports.fetch('schedule/basic',{'matchId':ids})
                    rows,stamp,url=verification_batches[batch]
                    matching=[r for r in rows if str(r.get('matchId'))==f['external_id'] and agrees(r,MatchObservation.model_validate(f))]
                    valid=len(matching)==1 and datetime.fromisoformat(f['kickoff'])>datetime.now(UTC)
                else:
                    if provider is None:raise ProviderError('Football fixture verification is not configured')
                    current=await provider._get(f"matches/{f['external_id']}",{})
                    kickoff=datetime.fromisoformat(current['utcDate'].replace('Z','+00:00'))
                    valid=(str(current['id'])==f['external_id'] and current['competition']['code']==f['league'] and
                           str(current['homeTeam']['id'])==f['home_id'] and str(current['awayTeam']['id'])==f['away_id'] and
                           current['status']=='TIMED' and kickoff==datetime.fromisoformat(f['kickoff']) and kickoff>datetime.now(UTC))
                    stamp=datetime.now(UTC).isoformat();url=f"https://api.football-data.org/v4/matches/{f['external_id']}"
                checks.append({'event_id':f['external_id'],'source':f['source'],'passed':valid,'checked_at':stamp,'source_url':url})
                if valid:
                    (verified_research if p.get('research_only') else verified).append(p)
                if len(verified)+len(verified_research)>=request.count:break
            except (ProviderError,KeyError,ValueError,TypeError):errors.append('A finalist could not be reverified and was excluded.')
    picks=select_picks(verified,request,datetime.now(UTC),settings.odds_max_age_seconds,started,settings.slip_min_probability)
    research=take_research(verified_research,picks,request,datetime.now(UTC),started,settings.odds_max_age_seconds)
    # AI latency must not let a fixture start unnoticed before output.
    picks=select_picks(picks,request,datetime.now(UTC),settings.odds_max_age_seconds,started,settings.slip_min_probability)
    research=take_research(research,picks,request,datetime.now(UTC),started,settings.odds_max_age_seconds)
    by_fixture={(a['fixture']['source'],a['fixture']['external_id']):a for a in final}
    for p in picks:
        a=by_fixture.get((p['fixture']['source'],p['fixture']['external_id']))
        if a:p['market_predictions']=market_predictions(a,datetime.now(UTC),settings.odds_max_age_seconds)
    coverage={'researched':len(final),'with_history':sum(bool(a.get('baseline')) for a in final)}
    reason=f'No selections met the {settings.slip_min_probability:.0%} minimum model estimate and fresh verified odds of {MIN_ODDS:.2f}+ with sufficient supporting data.'
    if not initial['analyses']:reason='No verified, unstarted fixtures were found for your selected leagues and date.'
    elif not picks:
        counts=Counter(g for a in final for g in a.get('missing',[]))
        fresh=any(started<=datetime.fromisoformat(a['fixture']['collected_at'])<=datetime.now(UTC) for a in final)
        if not fresh and errors:reason='Fixture refresh failed; cached matches were excluded. Run with --details for provider errors.'
        elif not any(a.get('quotes') for a in final) and any('quota exhausted' in g for g in counts):reason='The primary odds feed is out of credits and the alternative feed supplied no qualifying prices. Fresh verified odds of 1.50+ are required.'
        elif not any(a.get('quotes') for a in final) and any('authentication failed' in g or 'HTTP 401' in g for g in counts):reason='The odds provider rejected access. Check ODDS_API_KEY and your provider allowance; prices cannot be verified.'
        elif any('key is not configured' in g or 'provider configured' in g for g in counts):reason='A required data-provider key is missing from .env.'
        elif not any(a.get('ensemble') for a in final):reason='The selected matches do not have sufficient recent historical data.'
    result={'created_at':datetime.now(UTC).isoformat(),'request':{'count':request.count,'leagues':request.leagues,
        'day':request.day.isoformat(),'timezone':request.timezone,'min_probability':settings.slip_min_probability,'min_odds':MIN_ODDS,
        'ranking':'probability_then_expected_value'},'research_shortlist':research,'coverage':coverage,'gemini':gemini,'picks':picks,'reason':reason,'errors':errors,
        'analyses':final,'fixture_verifications':checks,'research_scan_id':initial['id']}
    save(engine,'terminal_slip',result)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description='Three questions → verified football research → a manual betting slip.')
    parser.add_argument('--details',action='store_true',help='Also print research gaps and source URLs')
    args=parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    engine=None
    try:
        settings=Settings()
        request=ask(settings.research_timezone)
        if not settings.database_url.startswith('sqlite:'):raise ValueError('This terminal version uses local SQLite. Set DATABASE_URL=sqlite:///betboot.db in .env.')
        with exclusive_run():
            engine=engine_for(settings.database_url);Base.metadata.create_all(engine)
            print('Researching… provider rate limits may take several minutes.',file=sys.stderr)
            result=asyncio.run(generate(request,settings,engine))
            folder=Path('artifacts/slips');folder.mkdir(parents=True,exist_ok=True)
            path=folder/(datetime.now(UTC).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8]+'.json')
            with path.open('x') as f:json.dump(result,f,indent=2)
            path.chmod(0o600)
            print('\n'+render(result['picks'],request,result.get('reason'),result.get('research_shortlist'),result.get('coverage'),details=args.details))
            if args.details:
                print('\nResearch record:',path)
                print('Gemini review:',json.dumps(result.get('gemini',{}),ensure_ascii=False))
                for error in result.get('errors',[]):print('-',error)
                for a in result['analyses']:
                    print(a['fixture']['home_name'],'vs',a['fixture']['away_name'],':','; '.join(a.get('reasons',[])+a.get('missing',[])))
                    for source in a['sources']:print(' ',source['url'],source['collected_at'])
        return 0
    except (EOFError,KeyboardInterrupt):
        print('\nCancelled. No bets were placed.');return 130
    except ValueError as exc:
        # Pydantic configuration errors can include raw input; do not print them.
        from pydantic import ValidationError
        print('Configuration error. Check .env.' if isinstance(exc,ValidationError) else str(exc),file=sys.stderr);return 2
    except Exception:
        print('NO RELIABLE PICKS FOUND\nResearch could not complete. Check provider access and local configuration.',file=sys.stderr);return 1
    finally:
        if engine is not None:engine.dispose()
