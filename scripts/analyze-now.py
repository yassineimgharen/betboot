"""Collect and analyze a window using configured data-provider keys; no bet execution."""
import argparse
import asyncio
import json
from betboot.config import Settings
from betboot.db import engine_for
from betboot.pipeline import scan

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--hours',type=int,choices=[24,48,72],default=24)
parser.add_argument('--match-id',help='Optional football-data.org match ID to analyze')
parser.add_argument('--deep',action='store_true',help='Request extended match-specific context')
args=parser.parse_args()
settings=Settings()
engine=engine_for(settings.database_url)
try:
    result=asyncio.run(scan(engine,settings,hours=args.hours,match_id=args.match_id,deep=args.deep))
    print(json.dumps({'summary':result['summary'],'scan_id':result['id'],'errors':result['errors'],
        'matches':[{'match':a['fixture']['home_name']+' vs '+a['fixture']['away_name'],'status':a['status'],
                    'missing':a['missing']} for a in result['analyses']]},indent=2))
finally:
    engine.dispose()
