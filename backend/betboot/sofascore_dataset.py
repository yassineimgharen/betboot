"""Audited, offline Sofascore result/xG dataset. No network access or inferred dates."""
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session
from betboot.store import Artifact, read, save
from betboot.sofascore_import import new_capture, add_response, repair_legacy, historical_summary


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def timestamp(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Timezone is required')
    return result


def positive_id(value):
    if isinstance(value,bool) or not str(value).isdigit() or int(value)<=0:
        raise ValueError('Invalid provider identity')
    return str(value)


def normalize_capture(raw,raw_bytes,now):
    if raw.get('schema_version')!=2:
        raw=repair_legacy(raw,raw_bytes)
    c=new_capture(raw['event_id'])
    for r in raw['responses']:
        if not add_response(c,r['url'],r['data'],collected_at=r.get('collected_at'),status=r.get('http_status'),
                            url_reconstructed=r.get('url_reconstructed',False)):
            raise ValueError('Capture contains a foreign event or conflicting identity')
        if r.get('http_status') not in (None,200):raise ValueError('Capture contains a failed response')
    target='event/'+c['event_id']
    events=[r for r in c['responses'] if r['endpoint']==target]
    stats=[r for r in c['responses'] if r['endpoint']==target+'/statistics']
    if not events:raise ValueError('No target event metadata')
    # Preserve a file as one snapshot. Mixed timestamped revisions are not silently reordered.
    for group in (events,stats):
        times=[timestamp(r['collected_at']) for r in group if r['collected_at']]
        if times!=sorted(times):raise ValueError('Response revisions are not chronological')
    metadata=events[-1];event=metadata['data']['event']
    if str(event['id'])!=c['event_id']:raise ValueError('Event ID mismatch')
    if event['status']['type']!='finished':raise ValueError('Offline training importer accepts finished results only')
    kickoff=datetime.fromtimestamp(event['startTimestamp'],UTC)
    if kickoff>=now:raise ValueError('Finished event has a future kickoff')
    scores=[]
    for side in ('home','away'):
        value=event[side+'Score'].get('normaltime')
        if type(value)!=int or not 0<=value<=100:raise ValueError('Explicit regulation-time scores are required')
        scores.append(value)
    # ALL stats may include extra time; exclude such events rather than mix durations.
    if any(event[s+'Score'].get(k) is not None for s in ('home','away') for k in ('extra1','extra2','overtime','penalties')):
        raise ValueError('Extra-time/penalty event is outside the xG model scope')
    tournament=event['tournament']['uniqueTournament']
    if tournament.get('category',{}).get('sport',{}).get('slug')!='football':raise ValueError('Expected football')
    summary=historical_summary(c)
    if any(i['type']=='conflicting_statistic' for i in summary['issues']):raise ValueError('Conflicting full-match statistics')
    if 'statistics' not in summary:raise ValueError('Incomplete match metadata')
    xg=summary['statistics'].get('expectedGoals')
    if xg and any(not 0<=xg[s]<=20 for s in ('home','away')):raise ValueError('xG outside supported range')
    used=[metadata]+([stats[-1]] if stats else [])
    for r in used:
        if r['collected_at'] and timestamp(r['collected_at'])<=kickoff:
            raise ValueError('Finished-match data claims a pre-kickoff collection time')
    capture_known=all(r['collected_at'] is not None for r in used)
    # Unknown capture times become usable from import onward, never backdated.
    available=max(timestamp(r['collected_at']) for r in used) if capture_known else now
    home,away=event['homeTeam'],event['awayTeam']
    if any(not isinstance(t.get('name'),str) or not t['name'].strip() for t in (home,away)):
        raise ValueError('Team names are required')
    row={'event_id':positive_id(event['id']),'league_id':positive_id(tournament['id']),
         'league_name':tournament['name'],'country':tournament['category']['name'],
         'season_id':positive_id(event['season']['id']),'season_name':event['season'].get('year'),
         'kickoff':kickoff.isoformat(),'home_id':positive_id(home['id']),'away_id':positive_id(away['id']),
         'home_name':home['name'],'away_name':away['name'],'home_goals':scores[0],'away_goals':scores[1],
         'home_xg':xg['home'] if xg else None,'away_xg':xg['away'] if xg else None,
         'capture_time_known':capture_known,'captured_at':available.isoformat() if capture_known else None,
         'statistics':summary['statistics'],'source_urls':[r['url'] for r in used]}
    if row['home_id']==row['away_id']:raise ValueError('A team cannot play itself')
    row['fingerprint']=digest(row)
    row.update({'available_at':available.isoformat(),'imported_at':now.isoformat(),
                'file_sha256':hashlib.sha256(raw_bytes).hexdigest(),
                'provenance_note':'Captured timestamps supplied in local file' if capture_known else 'Capture time unknown; available from import time only'})
    return row


def import_files(engine,paths):
    summary={'imported':0,'duplicates':0,'rejected':[],'files':[]}
    for filename in paths:
        path=Path(filename)
        try:
            data=path.read_bytes()
            if len(data)>50_000_000:raise ValueError('File exceeds 50 MB import limit')
            row=normalize_capture(json.loads(data),data,datetime.now(UTC))
            identifier=str(uuid5(NAMESPACE_URL,'betboot:sofascore:'+row['fingerprint']))
            with Session(engine) as db,db.begin():
                if db.get(Artifact,identifier):summary['duplicates']+=1;continue
                db.add(Artifact(id=identifier,kind='sofa_observation',created_at=datetime.now(UTC),payload=row))
            summary['imported']+=1;summary['files'].append({'file':path.name,'event_id':row['event_id'],'capture_time_known':row['capture_time_known']})
        except (OSError,ValueError,KeyError,TypeError,OverflowError,AttributeError,IndexError) as exc:
            summary['rejected'].append({'file':path.name,'reason':str(exc) if isinstance(exc,ValueError) else 'File or schema validation failed'})
    save(engine,'sofa_import',summary)
    return summary


def records(engine):
    with Session(engine) as db:
        return [r.payload for r in db.query(Artifact).filter_by(kind='sofa_observation').order_by(Artifact.created_at,Artifact.id).all()]


def versions_as_of(rows,cutoff):
    latest={}
    for r in sorted(rows,key=lambda r:(timestamp(r['available_at']),timestamp(r['imported_at']))):
        if timestamp(r['available_at'])<=cutoff:
            latest[r['event_id']]=r
    return list(latest.values())


def coverage(engine):
    rows=records(engine);latest=versions_as_of(rows,datetime.now(UTC));groups=[]
    for lid in sorted({r['league_id'] for r in latest}):
        group=[r for r in latest if r['league_id']==lid]
        groups.append({'league_id':lid,'league_name':group[0]['league_name'],'country':group[0]['country'],
                       'matches':len(group),'xg_matches':sum(r['home_xg'] is not None for r in group),
                       'known_capture_times':sum(r['capture_time_known'] for r in group)})
    return {'source':'Imported local Sofascore JSON','matches':len(latest),'revisions':len(rows),
            'leagues':groups,'recent_matches':sorted(latest,key=lambda r:r['kickoff'],reverse=True)[:10],
            'validations':read(engine,'sofa_validation',100),
            'note':'Unknown capture times are usable only from import onward. No timestamps are inferred from file dates.'}
