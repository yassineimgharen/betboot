"""Append-only record of the quotes seen on each run, so goals markets (which have no
historical odds source on the current plans) can be validated against the market later."""
from datetime import datetime
import json

from slipbot.config import ROOT
from slipbot.quotes import Quote

SNAPSHOTS = ROOT / 'data' / 'odds-snapshots.jsonl'


def record(fixtures, quotes, now):
    SNAPSHOTS.parent.mkdir(exist_ok=True)
    with SNAPSHOTS.open('a') as out:
        for m in fixtures:
            if m.id in quotes:
                out.write(json.dumps({
                    'recorded': now.isoformat(), 'match_id': m.id, 'league': m.league,
                    'kickoff': m.kickoff.isoformat(),
                    'quotes': [[x.bookmaker, x.market, x.selection, x.price, x.updated.isoformat()]
                               for x in quotes[m.id]]}) + '\n')


def latest_before_kickoff():
    """{match id: [Quote]} from each match's last snapshot recorded before kickoff."""
    latest = {}
    try:
        lines = SNAPSHOTS.read_text().splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            row = json.loads(line)
            recorded, kickoff = datetime.fromisoformat(row['recorded']), datetime.fromisoformat(row['kickoff'])
        except (ValueError, KeyError):
            continue
        if recorded < kickoff and (row['match_id'] not in latest or recorded > latest[row['match_id']][0]):
            latest[row['match_id']] = (recorded, row['quotes'])
    return {mid: [Quote(market=m, selection=s, price=p, bookmaker=b, updated=datetime.fromisoformat(u))
                  for b, m, s, p, u in qs]
            for mid, (_, qs) in latest.items()}
