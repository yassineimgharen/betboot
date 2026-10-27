"""Documented iSports current 1X2 prices; opening prices are never substituted."""
from datetime import UTC,datetime
import math
from betboot.providers.isports import agrees
from betboot.providers.base import ProviderError


def parse_quotes(rows,event,fixture,collected,url,max_age,now=None):
    now=now or datetime.now(UTC)
    if fixture.kickoff<=now:return []
    matching=[r for r in rows if str(r.get('matchId'))==str(event['matchId']) and
              r.get('leagueName')==event['leagueName'] and r.get('homeName')==event['homeName'] and
              r.get('awayName')==event['awayName'] and r.get('matchTime')==event['matchTime']]
    if len(matching)!=1:return []
    result=[]
    for book in matching[0].get('odds',[]):
        try:
            updated=datetime.fromtimestamp(float(book['changeTime']),UTC)
            if not 0<=(now-updated).total_seconds()<=max_age:continue
            fields=book['oddsDetail'].split(',')
            if len(fields)!=8 or not fields[0].isdigit() or not fields[1]:continue
            prices=[float(p) for p in fields[5:8]]
            if any(not math.isfinite(p) or p<=1 for p in prices):continue
            overround=sum(1/p for p in prices)
            if not .95<=overround<=1.3:continue
            for selection,price in zip(('home','draw','away'),prices):
                result.append({'selection':selection,'odds':price,'bookmaker':fields[1],
                    'bookmaker_key':'isports-'+fields[0],'updated_at':updated.isoformat(),'collected_at':collected,
                    'source':'iSports API','url':url,'event_id':str(event['matchId']),
                    'margin_removed_probability':(1/price)/overround})
        except (KeyError,ValueError,TypeError,AttributeError,OverflowError):continue
    return result


async def odds(provider,fixture,max_age):
    day=fixture.kickoff.astimezone(UTC).date().isoformat()
    rows,_,_=await provider.fetch('schedule/basic',{'date':day})
    matched=[r for r in rows if agrees(r,fixture)]
    if len(matched)!=1:return [],'iSports odds: no unique current fixture identity agreement'
    quotes=[];gaps=[]
    try:
        prices,collected,url=await provider.fetch('odds/european/all',{'date':day})
        quotes.extend(parse_quotes(prices,matched[0],fixture,collected,url,max_age))
    except ProviderError as exc:gaps.append(str(exc))
    try:
        payload,collected,url=await provider.fetch('odds/main')
        if len(payload)==1 and isinstance(payload[0],dict):
            quotes.extend(parse_totals(payload[0],matched[0],fixture,collected,url,max_age))
    except ProviderError as exc:gaps.append(str(exc))
    return quotes,None if quotes else '; '.join(gaps+['iSports odds: no fresh supported 1X2 or goals prices'])


BOOKMAKERS={'1':'Macauslot','3':'Crown','4':'Ladbrokes','7':'SNAI','8':'Bet365',
    '9':'William Hill','12':'Easybets','14':'Vcbet','17':'Mansion88','19':'Interwetten',
    '22':'10BET','24':'12bet','31':'Sbobet','35':'Wewbet','42':'18bet','48':'HK Jockey Club',
    '49':'Bwin','50':'1xbet'}


def parse_totals(payload,event,fixture,collected,url,max_age,now=None):
    now=now or datetime.now(UTC)
    if fixture.kickoff<=now or not agrees(event,fixture):return []
    quotes=[]
    for row in payload.get('overUnder',[]):
        try:
            fields=row.split(',')
            if len(fields)!=11 or fields[0]!=str(event['matchId']) or fields[1] not in BOOKMAKERS:continue
            # Only open full-match early/closing quotes; never in-play, unknown stage or halftime.
            if fields[9].casefold()!='false' or fields[10] not in ('1','2'):continue
            line=float(fields[5])
            if line not in (1.5,2.5,3.5):continue
            updated=datetime.fromtimestamp(float(fields[8]),UTC)
            if not 0<=(now-updated).total_seconds()<=max_age:continue
            prices=[float(fields[6])+1,float(fields[7])+1]  # Documented Hong Kong -> decimal.
            if any(not math.isfinite(p) or p<=1 for p in prices):continue
            if not .95<=sum(1/p for p in prices)<=1.3:continue
            for side,price in zip(('over','under'),prices):
                quotes.append({'selection':f'{side}_{line}','odds':price,'bookmaker':BOOKMAKERS[fields[1]],
                    'bookmaker_key':'isports-main-'+fields[1],'updated_at':updated.isoformat(),'collected_at':collected,
                    'source':'iSports API','url':url,'event_id':str(event['matchId']),
                    'margin_removed_probability':None})
        except (ValueError,TypeError,AttributeError,OverflowError):continue
    return quotes
