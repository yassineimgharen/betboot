"""Offline validation and capture bookkeeping; never requests remote data."""
from datetime import UTC, datetime
import hashlib
import json
import math
import re
from urllib.parse import urlsplit, parse_qsl


def new_capture(event_id):
    if not re.fullmatch(r'[1-9][0-9]*',str(event_id)):
        raise ValueError('Expected a numeric event ID')
    return {'schema_version':2,'event_id':str(event_id),'source':'Sofascore',
            'responses':[],'next_data_ssr':None,'issues':[]}


def add_response(capture,url,data,*,collected_at=None,status=None,url_reconstructed=False):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ('www.sofascore.com','api.sofascore.com'):
        raise ValueError('Unexpected source URL')
    match=re.fullmatch(r'/api/v1/event/([0-9]+)(/.*)?',parsed.path)
    if not match or match[1]!=capture['event_id']:
        return False
    if not isinstance(data,dict):
        capture['issues'].append({'type':'invalid_response','path':parsed.path});return False
    event=data.get('event')
    ids=[data.get('eventId')]
    if isinstance(event,dict):ids.append(event.get('id'))
    if any(value is not None and str(value)!=capture['event_id'] for value in ids):
        capture['issues'].append({'type':'event_identity_conflict','path':parsed.path});return False
    if collected_at is not None:
        stamp=datetime.fromisoformat(collected_at.replace('Z','+00:00'))
        if stamp.tzinfo is None or stamp>datetime.now(UTC):
            raise ValueError('Collection time must be timezone-aware and not in the future')
    capture['responses'].append({'url':url,'url_reconstructed':url_reconstructed,
        'request_parameters':None if url_reconstructed else parse_qsl(parsed.query,keep_blank_values=True),
        'endpoint':parsed.path.removeprefix('/api/v1/'),'collected_at':collected_at,
        'http_status':status,'data':data})
    return True


def attach_ssr(capture,data,*,collected_at=None):
    try: event=data['props']['pageProps']['event']
    except (KeyError,TypeError):
        capture['issues'].append({'type':'ssr_missing_identity'});return False
    if str(event.get('id'))!=capture['event_id']:
        capture['issues'].append({'type':'ssr_event_identity_conflict','actual_event_id':event.get('id'),
                                  'expected_event_id':capture['event_id']});return False
    # Keep only target-event data, not unrelated footer/session/geolocation data.
    capture['next_data_ssr']={'event':event,'collected_at':collected_at}
    return True


def repair_legacy(raw,original_bytes):
    capture=new_capture(raw['event_id'])
    capture['original_sha256']=hashlib.sha256(original_bytes).hexdigest()
    capture['processed_at']=datetime.now(UTC).isoformat()
    capture['issues'].append({'type':'legacy_provenance_missing',
        'detail':'Original collection timestamps and URL query strings were not saved; they remain unknown.'})
    unrelated=0
    for endpoint,data in raw.get('api_responses',{}).items():
        if not add_response(capture,'https://www.sofascore.com/api/v1/'+endpoint,data,url_reconstructed=True):
            unrelated+=1
    if raw.get('next_data_ssr') is not None:attach_ssr(capture,raw['next_data_ssr'])
    capture['excluded_response_count']=unrelated
    return capture


def historical_summary(capture):
    target='event/'+capture['event_id']
    events=[r for r in capture['responses'] if r['endpoint']==target and isinstance(r['data'].get('event'),dict)]
    event=events[-1]['data']['event'] if events else None
    report={'event_id':capture['event_id'],'pre_match_eligible':False,
        'usage':'Historical descriptive inputs only; not imported into prediction models',
        'issues':list(capture['issues']),'response_count':len(capture['responses'])}
    if not event:
        report['issues'].append({'type':'missing_event_metadata'});return report
    try:
        report['match']={'home':event['homeTeam']['name'],'away':event['awayTeam']['name'],
            'home_id':event['homeTeam']['id'],'away_id':event['awayTeam']['id'],
            'kickoff':datetime.fromtimestamp(event['startTimestamp'],UTC).isoformat(),
            'status':event['status']['type'],'competition':event['tournament']['name'],
            'season':event.get('season',{}).get('year')}
    except (KeyError,TypeError,ValueError):
        report['issues'].append({'type':'invalid_event_metadata'});return report
    report['collection_time_known']=all(r['collected_at'] is not None for r in capture['responses'])
    report['statistics']={}
    # ALL already contains both halves; duplicate summary groups must not be added.
    stats=[r for r in capture['responses'] if r['endpoint']==target+'/statistics']
    if stats:
        values={};conflicts=set()
        for period in stats[-1]['data'].get('statistics',[]):
            if period.get('period')!='ALL':continue
            for group in period.get('groups',[]):
                for item in group.get('statisticsItems',[]):
                    key=item.get('key')
                    if key not in ('expectedGoals','totalShotsOnGoal','shotsOnTarget','ballPossession'):continue
                    try:
                        pair=[float(str(item[side]).removesuffix('%')) for side in ('home','away')]
                        if not all(math.isfinite(n) and n>=0 for n in pair):continue
                    except (KeyError,TypeError,ValueError):continue
                    if key in values and values[key]!=pair:conflicts.add(key)
                    values[key]=pair
        report['statistics']={key:{'home':pair[0],'away':pair[1]} for key,pair in values.items() if key not in conflicts}
        for key in conflicts:report['issues'].append({'type':'conflicting_statistic','key':key})
    report['issues'].append({'type':'no_historical_prematch_proof',
        'detail':'Finished-match statistics and lineups are not evidence of what was available before that match.'})
    return report
