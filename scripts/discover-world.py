"""Discover global fixtures using the configured iSports catalog; no bet placement."""
import argparse
import asyncio
from datetime import UTC,datetime,timedelta
from collections import Counter
import httpx
from sqlalchemy.orm import Session
from betboot.config import Settings
from betboot.db import engine_for,as_of_matches
from betboot.providers.isports import ISportsContext
from betboot.providers.world import discover
from betboot.providers.base import ProviderError

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--hours',type=int,choices=[24,48,72],default=24)
parser.add_argument('--country',help='Exact provider country name, e.g. Bolivia')
args=parser.parse_args()
async def main():
    settings=Settings()
    if not settings.isports_api_key.get_secret_value():
        parser.exit(1,'ISPORTS_API_KEY is not configured\n')
    engine=engine_for(settings.database_url)
    try:
        start=datetime.now(UTC);end=start+timedelta(hours=args.hours)
        async with httpx.AsyncClient(follow_redirects=False) as client:
            runs,rejected=await discover(engine,ISportsContext(client,settings),start,end,args.country)
        with Session(engine) as session:
            rows=[r for r in as_of_matches(session,datetime.now(UTC)) if r.source=='isportsapi.com' and start<=r.kickoff<end and r.status=='TIMED']
        print('Upcoming global fixtures:',len(rows),'country/region count:',len({r.country for r in rows}),'rejected records:',rejected)
        print('Countries:',dict(sorted(Counter(r.country for r in rows).items())))
    except ProviderError as exc:
        parser.exit(1,str(exc)+'\n')
    finally:
        engine.dispose()
asyncio.run(main())
