"""Out-of-sample validation and calibration: python -m slipbot.validate

1. Walk-forward over the last completed season (weekly refits on earlier matches only):
   Dixon-Coles, Elo and their blend against base rates for every market the model prices
   (1X2, double chance, over/under 1.5/2.5/3.5, both teams to score), with the hit rate
   of selections rated at or above 65%. Fits the Dixon-Coles/Elo blend weight.
2. Bookmaker consensus on the iSports closing-odds archive (about one month): its own
   high-probability calibration (1X2 and double chance derived from it; extra leagues
   included), the model's pooling weight, and the new selection rule replayed.
3. Goals markets against quotes recorded by earlier runs of the bot.
Writes slipbot/calibration.json, including per-market `validated` flags the bot obeys.
"""
from datetime import UTC, datetime, timedelta
import json
import sys

import numpy as np

from slipbot import archive, engine, markets, quotes as q
from slipbot.config import load_settings
from slipbot.http import Fetcher, SourceError
from slipbot.leagues import BY_ISPORTS_NAME, FD_CODES, LEAGUES
from slipbot.models import dixon_coles, elo
from slipbot.sources.football_data import FootballData, season_of, utcnow
from slipbot.sources.isports import BEIJING, ISports, parse_european
from slipbot.teams import best_match

OUTCOMES = ('home', 'draw', 'away')
GOALS_MARKETS = tuple(f'OU{line}' for line in markets.GOAL_LINES) + ('BTTS',)
STATIC_MARKETS = ('1X2', 'DC') + GOALS_MARKETS
AH_MARKETS = tuple(markets.ah_market(h) for h in markets.AH_LINES)
# Extra football-data.org free-tier leagues used only to check the bookmaker consensus.
CONSENSUS_ONLY = {'BSA': 'Brazil Serie A'}


def outcome(m):
    return 'home' if m.home_goals > m.away_goals else 'draw' if m.home_goals == m.away_goals else 'away'


def week_start(day):
    return (day - timedelta(days=day.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def walk_forward(history, test_from, test_to):
    """Pre-match model probabilities for matches in [test_from, test_to), weekly refits."""
    _, diffs = elo.run(history)
    diff_of = {m.id: d for m, d in zip(history, diffs)}
    rows = []
    test = [m for m in history if test_from <= m.kickoff < test_to]
    for start in sorted({week_start(m.kickoff) for m in test}):
        train = [m for m in history if m.kickoff < start]
        if len(train) < engine.MIN_LEAGUE_MATCHES:
            continue
        dc = dixon_coles.fit(train, start, **engine.RESULT_FIT)
        goals = dixon_coles.fit(train, start, **engine.GOALS_FIT)
        link = elo.fit_link([diff_of[m.id] for m in train[120:]],
                            [OUTCOMES[::-1].index(outcome(m)) for m in train[120:]])
        base = base_rates(train)
        for m in test:
            if week_start(m.kickoff) != start:
                continue
            if any(t not in dc.teams or dc.recent.get(t, 0) < engine.MIN_TEAM_RECENT
                   for t in (m.home_id, m.away_id)):
                continue
            matrix = dc.score_matrix(m.home_id, m.away_id)
            p = markets.probabilities(matrix)
            g = markets.probabilities(goals.score_matrix(m.home_id, m.away_id))
            ah = {}
            for market in AH_MARKETS:
                for side, (won, lost) in markets.handicap(matrix, markets.ah_line(market)).items():
                    ah[(market, side)] = float(markets.decided_probability(won, lost))
            rows.append({'match': m, 'dc': {s: float(p[('1X2', s)]) for s in OUTCOMES},
                         'elo': elo.outcome_probabilities(diff_of[m.id], *link),
                         'goals': {(mk, s): float(g[(mk, s)]) for mk in GOALS_MARKETS
                                   for s in markets.selections(mk)},
                         'ah': ah, 'base': base})
    return rows


def decided_share_won(market, selection, m):
    """Fraction of the decided stake that was won (handicaps can half-win); None on a push."""
    won, lost = markets.settle(market, selection, m.home_goals, m.away_goals)
    return None if won + lost == 0 else won / (won + lost)


def base_rates(train):
    """Recent league frequencies of every selection: the no-skill benchmark."""
    recent = train[-760:]
    rates = {}
    for market in STATIC_MARKETS + AH_MARKETS:
        for s in markets.selections(market):
            outcomes = [y for m in recent if (y := decided_share_won(market, s, m)) is not None]
            rates[(market, s)] = float(np.mean(outcomes)) if outcomes else 0.5
    return rates


def log_loss(probs, actual):
    return float(-np.mean(np.log(np.clip([p[a] for p, a in zip(probs, actual)], 1e-12, None))))


def binary_log_loss(ps, ys):
    ps = np.clip(np.asarray(ps, float), 1e-12, 1 - 1e-12)
    ys = np.asarray(ys, float)
    return float(-np.mean(ys * np.log(ps) + (1 - ys) * np.log(1 - ps)))


def mix(a, b, w):
    return {s: w * a[s] + (1 - w) * b[s] for s in OUTCOMES}


def band(ps, hits, threshold=engine.HIGH_BAND):
    """Selections rated at or above the threshold: count, mean prediction, hit rate."""
    ps, hits = np.asarray(ps, float), np.asarray(hits, float)
    mask = ps >= threshold
    if not mask.any():
        return {'n': 0}
    return {'n': int(mask.sum()), 'predicted': round(float(ps[mask].mean()), 4),
            'observed': round(float(hits[mask].mean()), 4)}


def market_rows(rows, w):
    """{market family: [(p, outcome, base p)]} over every selection, from blended models.
    Handicap outcomes are the share of the decided stake won; pushes are left out."""
    out = {}
    for r in rows:
        m = r['match']
        blend = mix(r['dc'], r['elo'], w)
        probs = {('1X2', s): blend[s] for s in OUTCOMES}
        probs.update({('DC', s): v for s, v in engine.double_chance(blend).items()})
        probs.update(r['goals'])
        probs.update(r['ah'])
        for (market, s), p in probs.items():
            y = decided_share_won(market, s, m)
            if y is not None:
                out.setdefault(markets.family(market), []).append((p, y, r['base'][(market, s)]))
    return out


def part_one(fd, now):
    season = season_of(now.date()) - 1
    test_from, test_to = datetime(season, 7, 1, tzinfo=UTC), datetime(season + 1, 7, 1, tzinfo=UTC)
    rows = []
    for code in FD_CODES:
        league_rows = walk_forward(fd.history(code, now), test_from, test_to)
        print(f'  {code}: {len(league_rows)} test matches', flush=True)
        rows += league_rows
    actual = [outcome(r['match']) for r in rows]
    print(f'\n1X2, season {season}/{season + 1}, {len(rows)} matches, walk-forward weekly refits')
    base_1x2 = [{s: r['base'][('1X2', s)] for s in OUTCOMES} for r in rows]
    for name, probs in (('base rates', base_1x2), ('Elo', [r['elo'] for r in rows]),
                        ('Dixon-Coles', [r['dc'] for r in rows])):
        print(f'  {name:<24}log loss {log_loss(probs, actual):.4f}')
    losses = {round(w, 1): log_loss([mix(r['dc'], r['elo'], w) for r in rows], actual)
              for w in np.arange(0, 1.01, 0.1)}
    best_w = min(losses, key=losses.get)
    print(f'  {f"blend (DC weight {best_w})":<24}log loss {losses[best_w]:.4f}')

    print('\nEvery market, binary log loss per selection (model vs base rate), and selections '
          f'rated >= {engine.HIGH_BAND:.0%}:')
    record = {}
    for market, items in market_rows(rows, best_w).items():
        ps, hits, base = zip(*items)
        model_loss, base_loss = binary_log_loss(ps, hits), binary_log_loss(base, hits)
        high = band(ps, hits)
        record[market] = {'model_log_loss': round(model_loss, 4), 'base_log_loss': round(base_loss, 4),
                          'model_band': high, 'model_beats_base': model_loss < base_loss}
        shown = (f'predicted {high["predicted"]:.1%}, observed {high["observed"]:.1%}, n={high["n"]}'
                 if high['n'] else 'none')
        print(f'  {market:<6} model {model_loss:.4f}  base {base_loss:.4f}  '
              f'{"beats base" if model_loss < base_loss else "NO SKILL"}  | >= {engine.HIGH_BAND:.0%}: {shown}')
    return best_w, len(rows), record


def odds_archive(fd, isports, now, leagues):
    """[(code, finished match, pre-kickoff 1X2 quotes)] from the iSports archive."""
    names = {name: code for code, name in leagues.items()}
    rows_by_league = {}
    for day in sorted((now.astimezone(BEIJING) - timedelta(days=d)).date() for d in range(1, 36)):
        try:
            rows = isports.european_day(day, now)
        except SourceError as exc:
            print(f'  {day}: {exc}', file=sys.stderr)
            continue
        for r in rows:
            if r.get('leagueName') in names:
                rows_by_league.setdefault(names[r['leagueName']], []).append(r)
    found = []
    for code, rows in rows_by_league.items():
        history = (fd.history(code, now) if code in FD_CODES else
                   [m for m in fd.season(code, season_of(now.date()), now) if m.status == 'FINISHED'
                    and m.home_goals is not None])
        for m in history:
            candidates = [(r['homeName'], r['awayName'], r) for r in rows
                          if abs(float(r['matchTime']) - m.kickoff.timestamp()) <= 300]
            row = best_match((m.home, m.home_short), (m.away, m.away_short), candidates)
            if row:
                # Quotes changed after kickoff are in-play prices, never used.
                found.append((code, m, [x for x in parse_european(row) if x.updated < m.kickoff]))
    return found


def part_two(fd, isports, now, dc_weight, record, settings):
    print('\nBookmaker consensus on the iSports odds archive (60 s per new day; cached permanently)...',
          flush=True)
    leagues = {code: LEAGUES[code].isports_name for code in FD_CODES} | CONSENSUS_ONLY
    sample = odds_archive(fd, isports, now, leagues)
    consensus_items = {'1X2': [], 'DC': []}
    rows, fitted = [], {}
    for code, m, quotes in sample:
        book_map = q.books(quotes, '1X2', m.kickoff, 7 * 86400)
        if len(book_map) < engine.MIN_BOOKS:
            continue
        market = q.consensus(book_map, '1X2')
        for s in OUTCOMES:
            consensus_items['1X2'].append((market[s], markets.won('1X2', s, m.home_goals, m.away_goals)))
        for s, p in engine.double_chance(market).items():
            consensus_items['DC'].append((p, markets.won('DC', s, m.home_goals, m.away_goals)))
        if code not in FD_CODES:
            continue
        week = week_start(m.kickoff)
        if (code, week) not in fitted:
            fitted[code, week] = engine.fit_league([h for h in fd.history(code, now) if h.kickoff < week], week)
        model = fitted[code, week]
        if model is None or any(t not in model.dc.teams or model.dc.recent.get(t, 0) < engine.MIN_TEAM_RECENT
                                for t in (m.home_id, m.away_id)):
            continue
        p_model = engine.model_markets(model, m, {'dc_weight': dc_weight}, ['1X2'])['1X2'][0]
        prices = {s: q.price_to_show(book_map, s, settings.preferred_bookmaker) for s in OUTCOMES}
        rows.append({'match': m, 'model': p_model, 'market': market, 'prices': prices,
                     'spread': q.dispersion(book_map, '1X2')})

    matches = len(consensus_items['1X2']) // 3
    print(f'  consensus checked on {matches} matches ({", ".join(leagues)})')
    for market, items in consensus_items.items():
        ps, hits = zip(*items) if items else ((), ())
        record.setdefault(market, {})['market_band'] = band(ps, hits)
        b = record[market]['market_band']
        if b['n']:
            print(f'  {market:<4} consensus >= {engine.HIGH_BAND:.0%}: predicted {b["predicted"]:.1%}, '
                  f'observed {b["observed"]:.1%}, n={b["n"]}')

    if len(rows) < 50:
        print(f'  Only {len(rows)} top-league matches with odds: keeping the default pooling weight.')
        return None, len(rows)
    actual = [outcome(r['match']) for r in rows]
    losses = {round(w, 2): log_loss([engine.pool(r['model'], r['market'], w) for r in rows], actual)
              for w in np.arange(0, 1.01, 0.05)}
    best = min(losses, key=losses.get)
    rng = np.random.default_rng(1)
    boot = []
    for _ in range(300):
        idx = rng.integers(0, len(rows), len(rows))
        boot.append(min(losses, key=lambda w: log_loss([engine.pool(rows[i]['model'], rows[i]['market'], w)
                                                         for i in idx], [actual[i] for i in idx])))
    lo, hi = np.percentile(boot, [5, 95])
    print(f'\n1X2 model vs market, {len(rows)} top-league matches: market {losses[0.0]:.4f}, model '
          f'{losses[1.0]:.4f}, best pooling weight {best} (90% bootstrap {lo:.2f}-{hi:.2f})')

    # Replay of the selection rule (1X2 only: the archive has no other markets): the best-scored
    # pick per match, grouped by the internal confidence tier.
    tiers = {}
    for r, a in zip(rows, actual):
        pooled = engine.pool(r['model'], r['market'], best)
        options = []
        for s in OUTCOMES:
            quote = r['prices'][s]
            if quote and quote.price > settings.min_odds:
                sd = float(np.hypot(r['spread'][s], abs(r['model'][s] - r['market'][s]) / 2))
                c = engine.Candidate(r['match'], '1X2', s, quote, pooled[s], r['model'][s], r['market'][s], sd, 0, 0,
                                     {'validated': True})
                options.append(c)
        if options:
            pick = max(options, key=lambda c: c.score)
            tiers.setdefault(pick.tier, []).append((pick.probability, pick.selection == a, pick.quote.price))
    for tier in ('high', 'medium', 'speculative'):
        if tier in tiers:
            ps, hits, prices = zip(*tiers[tier])
            print(f'  Replayed picks, {tier:<11} tier: {len(ps):>3} picks, predicted {np.mean(ps):.1%}, '
                  f'won {np.mean(hits):.1%}, mean odds {np.mean(prices):.2f}')
    return best, len(rows)


def part_two_all_competitions(isports, now, record):
    """Bookmaker-consensus calibration in every competition iSports archives, split into the
    named competitions and all others. Results come from the iSports schedule (same match
    ids as the odds archive; it keeps about one month)."""
    print('\nConsensus in all competitions (needs one schedule request per day: about 35 minutes '
          'the first time, cached after)...', flush=True)
    archived = {}
    for day in sorted((now.astimezone(BEIJING) - timedelta(days=d)).date() for d in range(1, 36)):
        try:
            for r in isports.european_day(day, now):
                archived[str(r['matchId'])] = r
        except SourceError:
            continue
    results = {}
    for day in sorted((now - timedelta(days=d)).date() for d in range(1, 33)):
        try:
            rows = isports.past_schedule(day, now)
        except SourceError as exc:
            print(f'  schedule {day}: {exc}', file=sys.stderr)
            continue
        for r in rows:
            if r.get('status') == -1:
                results[str(r['matchId'])] = r
    items = {('named', '1X2'): [], ('named', 'DC'): [], ('other', '1X2'): [], ('other', 'DC'): []}
    for mid, row in archived.items():
        result = results.get(mid)
        if not result:
            continue
        kickoff = datetime.fromtimestamp(float(row['matchTime']), UTC)
        book_map = q.books([x for x in parse_european(row) if x.updated < kickoff], '1X2', kickoff, 7 * 86400)
        if len(book_map) < engine.MIN_BOOKS:
            continue
        group = 'named' if row.get('leagueName') in BY_ISPORTS_NAME else 'other'
        hg, ag = int(result['homeScore']), int(result['awayScore'])
        market = q.consensus(book_map, '1X2')
        for s in OUTCOMES:
            items[(group, '1X2')].append((market[s], markets.won('1X2', s, hg, ag)))
        for s, v in engine.double_chance(market).items():
            items[(group, 'DC')].append((v, markets.won('DC', s, hg, ag)))
    for (group, market), rows in items.items():
        if not rows:
            continue
        ps, hits = zip(*rows)
        b = band(ps, hits)
        loss = binary_log_loss(ps, hits)
        print(f'  {group:<5} competitions {market:<3}: {len(rows) // (3 if market == "1X2" else 3)} matches, '
              f'log loss {loss:.4f}; >= {engine.HIGH_BAND:.0%}: '
              + (f'predicted {b["predicted"]:.1%}, observed {b["observed"]:.1%}, n={b["n"]}' if b['n'] else 'none'))
        if group == 'other':
            record.setdefault(market, {})['market_band_other'] = b


def part_three(fd, now, dc_weight, record):
    """Goals markets vs. the market, from quotes recorded by earlier runs of the bot."""
    snapshots = archive.latest_before_kickoff()
    rows, fitted = [], {}
    for code in FD_CODES:
        history = fd.history(code, now)
        for m in (h for h in history if h.id in snapshots):
            week = week_start(m.kickoff)
            if (code, week) not in fitted:
                fitted[code, week] = engine.fit_league([h for h in history if h.kickoff < week], week)
            model = fitted[code, week]
            if model is None or any(t not in model.goals.teams for t in (m.home_id, m.away_id)):
                continue
            modelled = engine.model_markets(model, m, {'dc_weight': dc_weight}, GOALS_MARKETS)
            for market in GOALS_MARKETS:
                book_map = q.books(snapshots[m.id], market, m.kickoff, 7 * 86400)
                if len(book_map) >= engine.MIN_BOOKS:
                    first = markets.selections(market)[0]
                    hit = markets.won(market, first, m.home_goals, m.away_goals)
                    rows.append((market, modelled[market][0], q.consensus(book_map, market),
                                 first if hit else markets.selections(market)[1]))
    print(f'\nGoals markets vs market consensus: {len(rows)} recorded market observations')
    for market in GOALS_MARKETS:
        items = [(kp[s], s == a) for mk, _, kp, a in rows if mk == market for s in markets.selections(market)]
        if items:
            record.setdefault(market, {})['market_band'] = band(*zip(*items))
    if len(rows) < 200:
        print('  Too few recorded observations to fit a goals model weight (need 200); the market '
              'consensus is used, checked against the validated model. Each run of the bot records more.')
        return None, len(rows)
    losses = {round(w, 2): float(-np.mean([np.log(engine.pool(mp, kp, w)[a]) for _, mp, kp, a in rows]))
              for w in np.arange(0, 1.01, 0.05)}
    best = min(losses, key=losses.get)
    print(f'  market {losses[0.0]:.4f}, model {losses[1.0]:.4f}, best pooling weight {best}')
    return best, len(rows)


def validated(entry):
    """A market is selectable when the model beat base rates out of sample on it and its
    high-probability band has been checked on enough selections (model or market)."""
    bands = [entry.get('model_band', {}), entry.get('market_band', {})]
    return bool(entry.get('model_beats_base')) and any(b.get('n', 0) >= engine.MIN_BAND_SAMPLE for b in bands)


def main():
    settings = load_settings()
    fetcher = Fetcher(settings.cache_dir)
    now = utcnow()
    try:
        fd = FootballData(fetcher, settings.football_data_token)
        print('Part 1: walk-forward model validation', flush=True)
        dc_weight, n1, record = part_one(fd, now)
        model_weight, n2 = None, 0
        if settings.isports_key:
            isports = ISports(fetcher, settings.isports_key)
            model_weight, n2 = part_two(fd, isports, now, dc_weight, record, settings)
            part_two_all_competitions(isports, now, record)
        goals_weight, n3 = part_three(fd, now, dc_weight, record)
    except SourceError as exc:
        print(f'Validation stopped: {exc}', file=sys.stderr)
        return 1
    for entry in record.values():
        entry['validated'] = validated(entry)
    default = engine.DEFAULT_CALIBRATION
    result = {
        'dc_weight': float(dc_weight), 'dc_weight_matches': n1,
        'model_weight': float(model_weight) if model_weight is not None else default['model_weight'],
        'model_weight_matches': n2,
        'goals_model_weight': float(goals_weight) if goals_weight is not None else default['goals_model_weight'],
        'goals_model_weight_observations': n3,
        'markets': record,
        'source': f'python -m slipbot.validate, {now:%Y-%m-%d}'}
    engine.CALIBRATION_FILE.write_text(json.dumps(result, indent=2, default=bool) + '\n')
    print(f'\nWrote {engine.CALIBRATION_FILE.name}. Validated markets: '
          f'{", ".join(m for m, e in record.items() if e["validated"]) or "none"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
