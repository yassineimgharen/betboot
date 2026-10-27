from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
import math

from collections import Counter

import numpy as np
import pytest

from slipbot import app, command, engine, markets, quotes as q, selection, slip
from slipbot.leagues import LEAGUES
from slipbot.config import Settings
from slipbot.models import dixon_coles, elo
from slipbot.sources.football_data import Match
from slipbot.sources.isports import parse_all
from slipbot.teams import best_match, similarity

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


# ---- command parsing ---------------------------------------------------------------

@pytest.mark.parametrize('text,count', [
    ('Give me 5 matches.', 5), ('Generate a slip with 3 matches.', 3), ('3tini wr9a fiha 4 matchat.', 4),
    ('', 5), ('slip please', 5), ('3tini jouj matchat', 2), ('عطيني ٣ ماتشات', 3),
    ('six games in ligue 1', 6), ('donne-moi un ticket avec trois matchs', 3), ('100 matches', 20),
])
def test_count(text, count):
    assert command.parse(text, date(2026, 10, 26)).count == count


def test_leagues_and_dates():
    r = command.parse('2 picks serie a and bundesliga tomorrow', date(2026, 10, 26))
    assert r.leagues == ('SA', 'BL1') and r.day == date(2026, 10, 27) and r.count == 2
    assert command.parse('premier league saturday', date(2026, 10, 26)).day == date(2026, 10, 31)
    assert command.parse('matches on 2026-11-02', date(2026, 10, 26)).day == date(2026, 11, 2)
    assert command.parse('Bundesliga 4 matches', date(2026, 10, 26)).leagues == ('BL1',)
    assert command.parse('any 5', date(2026, 10, 26)).leagues == ()          # search widely
    assert command.parse('austrian bundesliga', date(2026, 10, 26)).leagues == ('AT1',)
    assert command.parse('portugal and turkey', date(2026, 10, 26)).leagues == ('PPL', 'TR1')


def test_stages_exclude_past_dates():
    from slipbot.app import stages
    past = command.Request(5, ('SA',), NOW.date() - timedelta(days=1))
    assert stages(past, NOW, _settings()) == []


# ---- team identity -------------------------------------------------------------------

def test_team_similarity():
    assert similarity('FC Internazionale Milano', 'Inter Milan') == 1
    assert similarity('VfL Wolfsburg', 'Wolfsburg') >= 0.9
    assert similarity('AS Roma', 'Lazio') < 0.55


def test_best_match_requires_unique_agreement():
    rows = [('AC Milan', 'Bologna', 1), ('Parma', 'Udinese', 2)]
    assert best_match(('AC Milan', 'Milan'), ('Bologna FC 1909', 'Bologna'), rows) == 1
    assert best_match(('Torino FC', 'Torino'), ('Como 1907', 'Como'), rows) is None


# ---- odds parsing and quotes ---------------------------------------------------------

def test_parse_all_odds_every_line():
    data = {'europeOdds': ['9,50,3.10,3.20,2.63,3.44,3.61,2.24,1,1792968027,false,2',
                           '9,47,2.78,3.56,2.39,3.30,3.66,2.19,1,1792968027,true,2',      # closed
                           '9,8,2.70,3.50,2.40,3.30,3.60,2.20,1,1792968027,false,3',      # in-play
                           '9,999,2.7,3.5,2.4,3.3,3.6,2.2,1,1792968027,false,2'],         # unknown book
            'overUnder': ['9,49,2.5,0.68,1.05,2.5,0.63,1.15,1,1793051402,false,2',
                          '9,49,1.5,0.30,2.40,1.5,0.28,2.60,2,1793051402,false,2',        # alternate line
                          '9,50,2.5,0.82,1.15,2.75,0.93,1.01,1,1792968027,false,1'],      # quarter line
            'handicap': ['9,50,0.5,0.90,0.95,0.5,0.95,0.90,false,true,1,2,1792968027,false,2',
                         '9,50,0,0.6,1.2,0,0.62,1.10,true,false,2,2,1792968027,false,2']}   # maintenance
    quotes = parse_all(data)['9']
    assert {(x.market, x.selection, x.price, x.bookmaker) for x in quotes} == {
        ('1X2', 'home', 3.44, '1xBet'), ('1X2', 'draw', 3.61, '1xBet'), ('1X2', 'away', 2.24, '1xBet'),
        ('OU2.5', 'over', 1.63, 'Bwin'), ('OU2.5', 'under', 2.15, 'Bwin'),
        ('OU1.5', 'over', 1.28, 'Bwin'), ('OU1.5', 'under', 3.6, 'Bwin'),
        ('AH-0.50', 'home', 1.95, '1xBet'), ('AH-0.50', 'away', 1.9, '1xBet')}   # +0.5: home gives 0.5


@pytest.mark.parametrize('market,outcome,point,expected', [
    ('Both Teams Score', 'Yes', None, ('BTTS', 'yes')), ('GG/NG', 'NG', None, ('BTTS', 'no')),
    ('Double Chance 1X', '1X', None, ('DC', '1X')), ('double_chance', 'Draw or Chelsea', None, ('DC', 'X2')),
    ('Total Goals', 'Over', 1.5, ('OU1.5', 'over')), ('Total Goals Over 1.5', 'Over 1.5', None, ('OU1.5', 'over')),
    ('Asian Handicap', 'Chelsea +0.5', None, ('AH-0.50', 'away')), ('spreads', 'Arsenal', -1.0, ('AH-1.00', 'home')),
    ('h2h', 'Arsenal', None, ('1X2', 'home')),
    ('Corners', 'Over', 9.5, None), ('totals', 'Over', 2.75, None), ('btts', 'Maybe', None, None),
])
def test_market_name_normalisation(market, outcome, point, expected):
    from slipbot.market_names import normalize
    assert normalize(market, outcome, point, 'Arsenal', 'Chelsea') == expected


def _quotes(book, prices, market='1X2', age_h=1):
    names = markets.selections(market)
    return [q.Quote(market, s, p, book, NOW - timedelta(hours=age_h)) for s, p in zip(names, prices)]


def test_consensus_removes_margin_and_filters():
    quotes = _quotes('A', (2.0, 3.4, 3.8)) + _quotes('B', (2.1, 3.3, 3.6)) + _quotes('Old', (2, 3, 4), age_h=99)
    books = q.books(quotes, '1X2', NOW, 48 * 3600)
    assert set(books) == {'A', 'B'}
    fair = q.consensus(books, '1X2')
    assert math.isclose(sum(fair.values()), 1)
    assert fair['home'] > 0.45


def test_price_to_show_prefers_bookmaker_and_rejects_outliers():
    quotes = (_quotes('1xBet', (2.0, 3.4, 3.8)) + _quotes('Pinnacle', (2.05, 3.5, 3.9))
              + _quotes('Bet365', (1.95, 3.4, 3.7)))
    books = q.books(quotes, '1X2', NOW, 48 * 3600)
    assert q.price_to_show(books, 'home', '1xBet').bookmaker == '1xBet'
    stale = (_quotes('1xBet', (2.9, 3.4, 2.6)) + _quotes('Pinnacle', (2.05, 3.5, 3.9))
             + _quotes('Bet365', (1.95, 3.4, 3.7)))
    shown = q.price_to_show(q.books(stale, '1X2', NOW, 48 * 3600), 'home', '1xBet')
    assert shown.bookmaker == 'Pinnacle'


# ---- models --------------------------------------------------------------------------

def _synthetic_league(seed=0, seasons=2):
    rng = np.random.default_rng(seed)
    strength = np.linspace(-0.5, 0.5, 10)
    matches, start, n = [], NOW - timedelta(days=700), 0
    for rnd in range(18 * seasons):
        order = rng.permutation(10)
        for h, a in zip(order[:5], order[5:]):
            n += 1
            hg = rng.poisson(np.exp(0.25 + 0.2 + strength[h] - strength[a] * 0.8))
            ag = rng.poisson(np.exp(0.25 + strength[a] - strength[h] * 0.8))
            matches.append(Match(n, 'SA', start + timedelta(days=rnd * 19), 'FINISHED', int(h), int(a),
                                 f'T{h}', f'T{a}', f'T{h}', f'T{a}', int(hg), int(ag)))
    return matches


def test_dixon_coles_recovers_strength_and_is_normalised():
    matches = _synthetic_league()
    model = dixon_coles.fit(matches, NOW, ridge=1)
    p = markets.probabilities(model.score_matrix(9, 0))
    assert p[('1X2', 'home')] > 0.6
    assert math.isclose(p[('1X2', 'home')] + p[('1X2', 'draw')] + p[('1X2', 'away')], 1)
    assert math.isclose(p[('OU2.5', 'over')] + p[('OU2.5', 'under')], 1)
    lam_h, lam_a = model.sampled_rates(9, 0, draws=50)
    batch = dixon_coles.score_matrix(lam_h, lam_a, model.rho)
    assert batch.shape == (50, 11, 11) and np.allclose(batch.sum(axis=(1, 2)), 1)


def test_elo_orders_teams():
    model = elo.fit(_synthetic_league())
    p = model.probabilities(9, 0)
    assert p['home'] > p['away'] and math.isclose(sum(p.values()), 1)


def test_settlement():
    assert markets.won('1X2', 'draw', 1, 1) and markets.won('OU2.5', 'over', 2, 1)
    assert markets.won('OU1.5', 'under', 1, 0) and not markets.won('OU3.5', 'over', 2, 1)
    assert markets.won('DC', '1X', 1, 1) and markets.won('DC', 'X2', 0, 2) and not markets.won('DC', '12', 2, 2)
    assert markets.won('BTTS', 'yes', 1, 2) and markets.won('BTTS', 'no', 0, 3)


def test_double_chance_quotes():
    from slipbot.sources.odds_api import double_chance_selection, parse_event
    assert double_chance_selection('Arsenal or Draw', 'Arsenal', 'Chelsea') == '1X'
    assert double_chance_selection('Draw/Away', 'Arsenal', 'Chelsea') == 'X2'
    assert double_chance_selection('Arsenal', 'Arsenal', 'Chelsea') is None
    event = {'home_team': 'Arsenal', 'away_team': 'Chelsea', 'bookmakers': [{'title': 'B', 'markets': [
        {'key': 'double_chance', 'last_update': '2026-10-26T10:00:00Z', 'outcomes': [
            {'name': 'Arsenal or Draw', 'price': 1.25}, {'name': 'Draw or Chelsea', 'price': 1.80},
            {'name': 'Arsenal or Chelsea', 'price': 1.30}]},
        {'key': 'btts', 'last_update': '2026-10-26T10:00:00Z', 'outcomes': [
            {'name': 'Yes', 'price': 1.70}, {'name': 'No', 'price': 2.10}]}]}]}
    quotes = parse_event(event)
    assert {(x.market, x.selection) for x in quotes} == {('DC', '1X'), ('DC', 'X2'), ('DC', '12'),
                                                         ('BTTS', 'yes'), ('BTTS', 'no')}
    books = q.books(quotes, 'DC', datetime(2026, 10, 26, 11, tzinfo=UTC), 3600)
    assert math.isclose(sum(q.consensus(books, 'DC').values()), 2)


# ---- selection and slip --------------------------------------------------------------

VALIDATED = {'validated': True}


def _settings(**kw):
    base = dict(football_data_token='x', isports_key='x', odds_api_key='', min_odds=1.30, extra_market_events=0,
                max_odds_age=48 * 3600, max_days=7, time_budget=45, timezone=ZoneInfo('Africa/Casablanca'),
                preferred_bookmaker='1xBet', cache_dir=None)
    return Settings(**{**base, **kw})


def _candidate(match_id, price, p, home=None, away=None, market='1X2', selection_='home', model=None,
               market_p=None, sd=0.01, reliability=VALIDATED, kickoff=None):
    home = f'Home{match_id}' if home is None else home
    away = f'Away{match_id}' if away is None else away
    m = Match(match_id, 'SA', kickoff or NOW + timedelta(days=1), 'TIMED', home, away, home, away, home, away)
    return engine.Candidate(m, market, selection_, q.Quote(market, selection_, price, '1xBet', NOW), p,
                            p if model is None else model, p if market_p is None else market_p, sd, 10, 1.0,
                            reliability)


def _assessments(*candidates):
    return [engine.Assessment(c.match, [c]) for c in candidates]


def test_longshots_rank_last_even_with_large_ev():
    """The old EV ranking picked Torino 5.14, Venezia 8.45: a few points of probability error
    times high odds looks like a big edge. The score is driven by probability instead."""
    longshots = [_candidate(1, 5.14, 0.25), _candidate(2, 8.45, 0.14), _candidate(3, 3.44, 0.33)]
    favourites = [_candidate(4, 1.36, 0.74, market='DC', selection_='1X'),
                  _candidate(5, 1.33, 0.73, market='OU1.5', selection_='over')]
    assert max(longshots + favourites, key=lambda c: c.ev).quote.price > 3     # what EV ranking chose
    picks = selection.choose(_assessments(*longshots, *favourites), 5)
    assert [p.match.id for p in picks] == [4, 5, 3, 1, 2]
    assert [p.match.id for p in selection.choose(_assessments(*longshots, *favourites), 2)] == [4, 5]


def test_requested_count_is_filled_from_real_candidates():
    pool = [_candidate(i, 1.4 + i / 10, 0.75 - i / 40) for i in range(1, 9)]
    assert len(selection.choose(_assessments(*pool), 6)) == 6
    assert len(selection.choose(_assessments(*pool), 20)) == 8          # never more than exist


def test_within_a_match_probability_beats_odds_and_ev():
    safe = _candidate(1, 1.32, 0.78, market='OU1.5', selection_='over')      # EV +3%
    rich = _candidate(1, 1.55, 0.70)                                         # EV +8.5%
    assert selection.best_per_match(engine.Assessment(safe.match, [rich, safe])) is safe


def test_uncertainty_lowers_rank():
    precise, noisy = _candidate(1, 1.40, 0.75, sd=0.01), _candidate(2, 1.40, 0.77, sd=0.06)
    assert [p.match.id for p in selection.choose(_assessments(noisy, precise), 5)] == [1, 2]


def test_tiers():
    assert _candidate(1, 1.4, 0.75).tier == 'high'
    assert _candidate(1, 1.8, 0.60).tier == 'medium'
    assert _candidate(1, 3.0, 0.35).tier == 'speculative'


def test_no_duplicate_fixture_or_team():
    a = _candidate(1, 1.5, 0.8, 'Inter', 'Lazio')
    same_fixture = _candidate('is:9', 1.5, 0.79, 'FC Internazionale Milano', 'SS Lazio', kickoff=a.match.kickoff)
    same_team = _candidate(3, 1.5, 0.78, 'Inter Milan', 'Roma', kickoff=NOW + timedelta(days=4))
    picks = selection.choose(_assessments(a, same_fixture, same_team), 5)
    assert picks == [a]


def test_slip_output_and_probability():
    picks = selection.choose(_assessments(_candidate(1, 1.45, 0.75), _candidate(2, 1.40, 0.80)), 2)
    text = slip.render(picks, 2, _settings(), date(2026, 10, 27))
    assert text.startswith('⚽ MY BETTING SLIP — 27/10/2026')
    assert 'Probability: 75%' in text and 'TOTAL COMBINED ODDS: 2.03' in text
    assert 'ESTIMATED SLIP PROBABILITY: 60.0%' in text and 'Only' not in text
    short = slip.render(picks[:1], 6, _settings(), date(2026, 10, 27))
    assert 'Only 1 of 6' in short and 'on 27/10/2026' in short


def test_unvalidated_market_is_flagged_and_penalised():
    btts = _candidate(1, 1.6, 0.66, market='BTTS', selection_='yes', reliability={'validated': False})
    assert 'less reliable market' in slip.render([btts], 1, _settings(), date(2026, 10, 27))


def test_slip_probability_assumes_independence_across_distinct_matches():
    point, cautious = selection.slip_probability([_candidate(1, 1.4, 0.8), _candidate(2, 1.4, 0.75)])
    assert math.isclose(point, 0.6) and cautious < point


def _league_model():
    league = _synthetic_league()
    return league, engine.LeagueModel(dixon_coles.fit(league, NOW), dixon_coles.fit(league, NOW, ridge=60),
                                      elo.fit(league), len(league))


QUOTES = [x for b, p in (('1xBet', (1.30, 5.0, 9.0)), ('Pinnacle', (1.33, 5.2, 9.5)), ('Bet365', (1.29, 5.0, 9.0)))
          for x in _quotes(b, p)]
CAL = {'dc_weight': 0.5, 'model_weight': 0.0, 'goals_model_weight': 0.0,
       'markets': {'1X2': {'validated': True, 'market_band': {'n': 100, 'predicted': 0.75, 'observed': 0.70}}}}


def test_assess_with_model_enforces_strict_minimum_odds():
    _, model = _league_model()
    fixture = Match(999, 'SA', NOW + timedelta(days=1), 'TIMED', 9, 0, 'T9', 'T0', 'T9', 'T0')
    result = engine.assess(fixture, model, QUOTES, NOW, _settings(), CAL)
    assert {c.selection for c in result.candidates} == {'draw', 'away'}     # 1.30 is not > 1.30
    assert all(c.model_probability is not None and c.uncertainty > 0 for c in result.candidates)


def test_assess_without_model_uses_market_with_extra_uncertainty():
    fixture = Match('is:1', 'TR1', NOW + timedelta(days=1), 'TIMED', 'a', 'b', 'A', 'B', 'A', 'B')
    result = engine.assess(fixture, None, QUOTES, NOW, _settings(), CAL)
    assert result.candidates and all(c.model_probability is None for c in result.candidates)
    assert all(c.uncertainty >= engine.MARKET_ONLY_SD for c in result.candidates)
    assert engine.assess(fixture, None, [], NOW, _settings(), CAL).problem == 'no verified bookmaker odds'


def test_market_overconfidence_is_subtracted():
    assert math.isclose(engine.overconfidence({'n': 100, 'predicted': 0.78, 'observed': 0.72}), 0.06)
    assert engine.overconfidence({'n': 10, 'predicted': 0.9, 'observed': 0.5}) == 0     # too small to trust
    assert engine.overconfidence({'n': 100, 'predicted': 0.70, 'observed': 0.75}) == 0  # never inflated


def test_model_cache_reused_until_new_results(tmp_path, monkeypatch):
    league = _synthetic_league(seasons=4)
    first = engine.cached_league('SA', league, NOW, tmp_path)
    monkeypatch.setattr(engine, 'fit_league', lambda *a: pytest.fail('refitted unchanged history'))
    again = engine.cached_league('SA', league, NOW, tmp_path)
    assert np.array_equal(first.dc.theta, again.dc.theta)
    monkeypatch.undo()
    assert engine.cached_league('SA', league[:-1], NOW, tmp_path) is not None     # new key: refit


# ---- discovery and search ------------------------------------------------------------

def _row(mid, league, home, away, kickoff, status=0):
    return {'matchId': mid, 'leagueName': league, 'homeName': home, 'awayName': away,
            'matchTime': kickoff.timestamp(), 'status': status}


def test_discover_isports_fixtures():
    from slipbot.odds import discover_isports
    k = NOW + timedelta(days=2)
    rows = [_row('1', 'Turkey Super Lig', 'Fenerbahce', 'Goztepe', k),
            _row('2', 'Turkey Super Lig', 'Besiktas', 'Rizespor', k, status=-1),        # finished
            _row('3', 'Turkey Super Lig', 'Trabzonspor', 'Kasimpasa', k),               # no open odds
            _row('4', 'Italy Serie A', 'Roma', 'Lazio', k),                              # football-data league
            _row('5', 'Turkey Super Lig', 'Antalya', 'Konya', NOW + timedelta(days=9))]  # outside window
    found = discover_isports(rows, ('TR1', 'SA'), NOW, NOW + timedelta(days=7), {'1': [], '2': [], '4': [], '5': []})
    assert [(m.id, m.league, m.isports_id) for m in found] == [('is:1', 'TR1', '1')]
    cup = rows + [_row('6', 'Spanish Copa Del Rey', 'Tudelano', 'Zaragoza', k)]
    found = discover_isports(cup, ('TR1', '*'), NOW, NOW + timedelta(days=7), {'1': [], '6': []})
    assert {(m.id, m.league) for m in found} == {('is:1', 'TR1'), ('is:6', 'Spanish Copa Del Rey')}


def test_link_isports_matches_league_kickoff_and_names():
    from slipbot.odds import link_isports
    k = NOW + timedelta(days=1)
    fixture = Match(7, 'SA', k, 'TIMED', 1, 2, 'FC Internazionale Milano', 'SS Lazio', 'Inter', 'Lazio')
    rows = [_row('11', 'Italy Serie A', 'Inter Milan', 'Lazio', k),
            _row('12', 'Italy Serie A', 'Inter Milan', 'Lazio', k + timedelta(hours=2))]
    assert link_isports([fixture], rows)[0].isports_id == '11'
    assert link_isports([fixture], rows[1:])[0].isports_id is None


def test_search_stages_stay_on_the_requested_day():
    from slipbot.app import stages, utc_days
    day = date(2026, 10, 30)
    scopes = stages(command.Request(6, (), day), NOW, _settings())
    assert [len(c) for c, _, _ in scopes] == [5, len(LEAGUES) + 1]      # every competition, never more days
    assert '*' in scopes[1][0]
    assert all(s.astimezone(ZoneInfo('Africa/Casablanca')).date() == day and
               e == datetime(2026, 10, 31, tzinfo=ZoneInfo('Africa/Casablanca')) for _, s, e in scopes)
    assert [c for c, _, _ in stages(command.Request(3, ('TR1',), day), NOW, _settings())] == [('TR1',)]
    assert len(utc_days(NOW, NOW + timedelta(days=7))) == 8


def test_deadline_defers_rate_limited_requests_and_serves_stale(tmp_path):
    import time
    from slipbot.http import Deferred, Fetcher
    fetcher = Fetcher(tmp_path, deadline=time.time() - 1)
    fetcher.wait_turn('x', 0)                      # nothing to wait for: allowed
    with pytest.raises(Deferred):
        fetcher.wait_turn('x', 60)                 # would have to wait 60 s past the deadline
    fetcher.store('k', {'cached': True})
    assert fetcher.get_cached('https://example.invalid', cache_key='k', max_age=-1, stale_ok=True,
                              slot='x', spacing=60) == {'cached': True}


# ---- strict date, timezone and end-to-end selection ----------------------------------

CASA = ZoneInfo('Africa/Casablanca')
JULY_NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)       # Morocco is UTC+1 in July 2026
DAY = date(2026, 7, 15)


def _fixture(mid, kickoff, home, away, league='TR1'):
    return Match(f'is:{mid}', league, kickoff, 'TIMED', home.lower(), away.lower(), home, away, home, away,
                 isports_id=str(mid))


def _book(mid, prices=(1.45, 4.6, 7.5), ou=(1.40, 2.95)):
    quotes = []
    for book, bump in (('1xBet', 1.0), ('Pinnacle', 1.02), ('Bet365', 0.98)):
        quotes += [q.Quote('1X2', s, round(p * bump, 2), book, JULY_NOW) for s, p in zip(('home', 'draw', 'away'), prices)]
        quotes += [q.Quote('OU2.5', s, round(p * bump, 2), book, JULY_NOW) for s, p in zip(('over', 'under'), ou)]
    return quotes


class FakeResearch:
    """Stands in for the providers: returns whatever fixtures it is given, even off-day ones."""
    fixtures_given, quotes_given = [], {}

    def __init__(self, settings, now, start, end, day_start=None):
        self.models, self.notes, self.deferred, self.diag = {}, [], False, Counter()

    def fixtures(self, codes, start, end):
        return list(self.fixtures_given)

    def quotes(self, fixtures):
        return {m.id: self.quotes_given[m.id] for m in fixtures if m.id in self.quotes_given}


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(app, 'utcnow', lambda: JULY_NOW)
    monkeypatch.setattr(app, 'Research', FakeResearch)
    monkeypatch.setattr(app.archive, 'record', lambda *a: None)
    record = {m: {'validated': True} for m in ('1X2', 'DC', 'OU2.5', 'AH', 'BTTS')}
    monkeypatch.setattr(engine, 'calibration', lambda: {**CAL, 'markets': record})
    return FakeResearch


def test_requested_date_never_returns_other_days_and_times_are_casablanca(offline):
    on_day_early = _fixture(1, datetime(2026, 7, 14, 23, 30, tzinfo=UTC), 'Alpha', 'Beta')   # 00:30 on the 15th
    on_day_late = _fixture(2, datetime(2026, 7, 15, 21, 0, tzinfo=UTC), 'Gamma', 'Delta')    # 22:00 on the 15th
    next_day = _fixture(3, datetime(2026, 7, 15, 23, 30, tzinfo=UTC), 'Eps', 'Zeta')         # 00:30 on the 16th
    previous_day = _fixture(4, datetime(2026, 7, 14, 22, 30, tzinfo=UTC), 'Eta', 'Theta')    # 23:30 on the 14th
    offline.fixtures_given = [on_day_early, on_day_late, next_day, previous_day]
    offline.quotes_given = {m.id: _book(m.id) for m in offline.fixtures_given}
    text = app.build_slip(command.Request(10, (), DAY), _settings())
    assert '15/07/2026' in text and 'Alpha vs Beta  ·  00:30' in text and 'Gamma vs Delta  ·  22:00' in text
    assert 'Eps' not in text and 'Eta' not in text
    assert 'Only 2 of 10' in text                              # insufficient fixtures: no padding


def test_started_matches_are_excluded(offline):
    started = _fixture(1, JULY_NOW - timedelta(minutes=5), 'Alpha', 'Beta')
    about_to_start = _fixture(2, JULY_NOW + timedelta(minutes=5), 'Gamma', 'Delta')
    today_match = _fixture(3, JULY_NOW + timedelta(hours=6), 'Eps', 'Zeta')
    offline.fixtures_given = [started, about_to_start, today_match]
    offline.quotes_given = {m.id: _book(m.id) for m in offline.fixtures_given}
    text = app.build_slip(command.Request(5, (), JULY_NOW.date()), _settings())
    assert 'Eps vs Zeta' in text and 'Alpha' not in text and 'Gamma' not in text


def test_missing_odds_never_produce_predictions(offline):
    offline.fixtures_given = [_fixture(1, datetime(2026, 7, 15, 18, tzinfo=UTC), 'Alpha', 'Beta')]
    offline.quotes_given = {}
    text = app.build_slip(command.Request(3, (), DAY), _settings())
    assert text.startswith('❌') and 'Probability' not in text
    thin = [x for x in _book('x') if x.bookmaker == '1xBet']       # one bookmaker: no consensus
    fixture = offline.fixtures_given[0]
    assert engine.assess(fixture, None, thin, JULY_NOW, _settings(), CAL).candidates == []


def test_past_date_is_refused(offline):
    assert 'has already passed' in app.build_slip(command.Request(3, (), date(2026, 7, 13)), _settings())


def test_multiple_markets_are_evaluated_and_the_best_one_wins():
    fixture = _fixture(1, datetime(2026, 7, 15, 18, tzinfo=UTC), 'Alpha', 'Beta')
    quotes = _book(1, prices=(2.6, 3.3, 2.7), ou=(1.36, 3.10))
    for book, bump in (('1xBet', 1.0), ('Pinnacle', 1.02), ('Bet365', 0.98)):
        quotes += [q.Quote('AH+0.50', s, round(p * bump, 2), book, JULY_NOW) for s, p in zip(('home', 'away'), (1.55, 2.45))]
    cal = {**CAL, 'markets': {m: {'validated': True} for m in ('1X2', 'OU2.5', 'AH')}}
    result = engine.assess(fixture, None, quotes, JULY_NOW, _settings(), cal)
    assert {c.market for c in result.candidates} == {'1X2', 'OU2.5', 'AH+0.50'}
    best = selection.best_per_match(result)
    assert best.market == 'OU2.5' and best.selection == 'over'      # not a 1X2 pick by default
    assert len(selection.choose([result], 5)) == 1                  # one selection per match


def test_prompt_answers():
    today = date(2026, 10, 27)
    assert command.parse_date('30/10/2026', today) == date(2026, 10, 30)
    assert command.parse_date('Today', today) == today and command.parse_date('', today) == today
    assert command.parse_date('tomorrow', today) == date(2026, 10, 28)
    assert command.parse_date('31/02/2026', today) is None and command.parse_date('soon', today) is None
    assert command.parse_count('8') == 8 and command.parse_count('') is None and command.parse_count('x') is None


# ---- Asian handicap ------------------------------------------------------------------

def _matrix():
    return dixon_coles.score_matrix(1.6, 1.1, -0.05)


def test_half_line_handicaps_match_result_markets():
    m = _matrix()
    p = markets.probabilities(m)
    won, lost = markets.handicap(m, -0.5)['home']
    assert math.isclose(won, p[('1X2', 'home')]) and math.isclose(won + lost, 1)
    won, lost = markets.handicap(m, -0.5)['away']                    # away +0.5 = X2
    assert math.isclose(won, p[('DC', 'X2')])


def test_whole_line_handicap_pushes_and_quarter_line_splits():
    m = _matrix()
    won, lost = markets.handicap(m, -1.0)['home']
    i, j = np.meshgrid(np.arange(11), np.arange(11), indexing='ij')
    push = m[(i - j) == 1].sum()
    assert math.isclose(won + lost + push, 1)
    assert markets.settle('AH-1.00', 'home', 2, 1) == (0, 0)             # push
    assert markets.settle('AH-0.75', 'home', 2, 1) == (0.5, 0)           # half won
    assert markets.settle('AH-0.25', 'home', 1, 1) == (0, 0.5)           # half lost
    assert markets.settle('AH+0.25', 'away', 1, 1) == (0, 0.5)           # away -0.25 on a draw: half lost


def test_unlisted_competitions_are_priced_with_extra_uncertainty():
    named = Match('is:1', 'TR1', NOW + timedelta(days=1), 'TIMED', 'a', 'b', 'A', 'B', 'A', 'B')
    cup = Match('is:2', 'Spanish Copa Del Rey', NOW + timedelta(days=1), 'TIMED', 'c', 'd', 'C', 'D', 'C', 'D')
    a = engine.assess(named, None, QUOTES, NOW, _settings(), CAL).candidates[0]
    b = engine.assess(cup, None, QUOTES, NOW, _settings(), CAL).candidates[0]
    assert b.uncertainty > a.uncertainty and b.probability == a.probability


def test_rejections_are_counted_for_debug():
    fixture = Match('is:1', 'TR1', NOW + timedelta(days=1), 'TIMED', 'a', 'b', 'A', 'B', 'A', 'B')
    thin = [x for x in _quotes('Solo', (1.9, 2.1), market='OU3.5')]
    result = engine.assess(fixture, None, QUOTES + thin, NOW, _settings(), CAL)
    assert result.rejected['odds not above 1.30'] == 1
    assert result.rejected['market quoted by fewer than 3 bookmakers'] == 2
    log = Counter()
    picks = selection.choose([result], 1, log)
    assert len(picks) == 1 and log['other selections in a match whose best selection ranks higher'] == 1


def test_debug_report_lists_pipeline_counts():
    result = engine.assess(Match('is:1', 'TR1', NOW + timedelta(days=1), 'TIMED', 'a', 'b', 'A', 'B', 'A', 'B'),
                           None, QUOTES, NOW, _settings(), CAL)
    text = slip.render_debug(Counter({'fixtures on the selected date': 1}), [result], [], Counter(), _settings(),
                             date(2026, 10, 27))
    assert 'fixtures on the selected date' in text and 'odds not above 1.30' in text
    assert 'Matches with at least one eligible selection: 1' in text
