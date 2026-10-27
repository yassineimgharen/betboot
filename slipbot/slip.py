"""Plain-text betting slip."""
import math

from slipbot import selection
from slipbot.teams import display_name


def _probability(pick):
    text = f'{pick.probability:.0%}'
    if pick.settled_share < 0.995:
        # Whole/quarter handicap lines: the shown figure is P(win) when the stake is not returned.
        text += f' (stake back {1 - pick.settled_share:.0%})'
    return text + ('' if pick.validated else ' (less reliable market)')


def render(picks, requested, settings, day):
    lines = [f'⚽ MY BETTING SLIP — {day:%d/%m/%Y}', '']
    for n, pick in enumerate(picks, 1):
        m = pick.match
        lines += [f'{n}. {display_name(m.home_short)} vs {display_name(m.away_short)}'
                  f'  ·  {m.kickoff.astimezone(settings.timezone):%H:%M}',
                  f'   ✅ {pick.label}',
                  f'   📊 Odds: {pick.quote.price:.2f} ({pick.quote.bookmaker})',
                  f'   🎯 Probability: {_probability(pick)}', '']
    point, _ = selection.slip_probability(picks)
    lines.append(f'TOTAL COMBINED ODDS: {math.prod(p.quote.price for p in picks):.2f}')
    lines.append(f'ESTIMATED SLIP PROBABILITY: {point:.1%} (assumes independent matches)')
    if len(picks) < requested:
        lines.append(f'\nOnly {len(picks)} of {requested}: no other match on {day:%d/%m/%Y} has verified '
                     f'odds above {settings.min_odds:.2f} with enough data.')
    lines.append(f'Times: {settings.timezone.key}. Estimates, not guarantees. Odds move: confirm before betting.')
    return '\n'.join(lines)


def render_details(assessments, picks, settings):
    """Internal research view for --details."""
    chosen = {id(p) for p in picks}
    out = []
    for a in sorted(assessments, key=lambda a: a.match.kickoff):
        m = a.match
        source = ('no odds' if not a.candidates else 'model+market'
                  if any(c.model_probability is not None for c in a.candidates) else 'market only')
        out.append(f'{m.kickoff.astimezone(settings.timezone):%d/%m %H:%M}  {m.league:<4} '
                   f'{m.home} vs {m.away}  [{source}, {len({c.market for c in a.candidates})} markets]')
        if a.problem:
            out.append(f'    skipped: {a.problem}')
        for c in sorted(a.candidates, key=selection.rank_key, reverse=True)[:8]:
            model = '  -  ' if c.model_probability is None else f'{c.model_probability:4.0%}'
            out.append(f'    {"*" if id(c) in chosen else " "} {c.label:<40} {c.quote.price:>5.2f} '
                       f'{c.quote.bookmaker:<9} est {c.probability:4.0%} model {model} '
                       f'market {c.market_probability:4.0%} ±{c.uncertainty:4.1%} score {c.score:.3f} '
                       f'{c.tier:<11} EV {c.ev:+5.1%} books {c.books:>2}{"" if c.validated else "  unvalidated"}')
    return '\n'.join(out)


def render_debug(diag, assessments, picks, choose_log, settings, day):
    """Pipeline counts for --debug: where fixtures, markets and selections are lost."""
    from collections import Counter
    lines = [f'── DEBUG {day:%d/%m/%Y} ({settings.timezone.key}) ──', 'Fixtures:']
    lines += [f'  {k:<62}{v:>6}' for k, v in diag.items()]
    lines.append(f'  {"fixtures analysed":<62}{len(assessments):>6}')
    problems = Counter(a.problem for a in assessments if a.problem)
    lines += [f'    skipped: {k:<51}{v:>6}' for k, v in problems.most_common()]
    rejected = sum((a.rejected for a in assessments), Counter())
    candidates = [c for a in assessments for c in a.candidates]
    lines += ['Markets and selections:',
              f'  {"betting markets quoted (all matches)":<62}{sum(a.quoted_markets for a in assessments):>6}',
              f'  {"selections priced by 3+ bookmakers":<62}{len(candidates) + sum(v for k, v in rejected.items() if "odds not" in k or "median" in k):>6}',
              f'  {f"selections with odds above {settings.min_odds:.2f} (candidates)":<62}{len(candidates):>6}',
              f'  {"  of which cross-checked by a statistical model":<62}{sum(c.model_probability is not None for c in candidates):>6}',
              f'  {"  of which in markets validated out of sample":<62}{sum(c.validated for c in candidates):>6}']
    by_market = Counter(c.market if not c.market.startswith('AH') else 'Asian handicap' for c in candidates)
    lines.append('  candidates by market: ' + ', '.join(f'{m} {n}' for m, n in by_market.most_common()))
    lines.append('Rejected selections (reason: count):')
    lines += [f'  {k:<62}{v:>6}' for k, v in (rejected + choose_log).most_common()]
    eligible = [a for a in assessments if a.candidates]
    lines += [f'Matches with at least one eligible selection: {len(eligible)}; on the slip: {len(picks)}',
              'Eligible matches by competition:']
    comps = Counter(a.match.league for a in eligible)
    lines += [f'  {n:>4}  {league}' for league, n in comps.most_common(20)]
    if len(comps) > 20:
        lines.append(f'  ... and {len(comps) - 20} more competitions')
    return '\n'.join(lines) + '\n'
