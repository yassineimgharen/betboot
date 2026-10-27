"""python main.py  (asks: how many matches, which date)   or   python main.py "8 matches 30/10/2026" """
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time as day_start, timedelta
import argparse
import subprocess
import sys
import time

from slipbot import archive, command, engine, odds, selection, slip
from slipbot.config import ROOT, load_settings
from slipbot.http import Deferred, Fetcher, SourceError
from collections import Counter

from slipbot.leagues import ALL_COMPETITIONS, CORE, FD_CODES, LEAGUES
from slipbot.sources.football_data import FootballData, utcnow
from slipbot.sources.isports import ISports

START_BUFFER = timedelta(minutes=10)    # never offer a match about to kick off


def today(settings, now=None):
    return (now or utcnow()).astimezone(settings.timezone).date()


def day_window(day, now, settings):
    """[start, end) of one calendar day in the configured timezone (Africa/Casablanca),
    starting no earlier than now + START_BUFFER so started matches never qualify."""
    local = datetime.combine(day, day_start.min, settings.timezone)
    return max(local, now + START_BUFFER), datetime.combine(day + timedelta(days=1), day_start.min,
                                                             settings.timezone)


def stages(request, now, settings):
    """Search scopes [(league codes, start, end)] for the requested day only, narrowest first:
    the top five leagues, then every competition with odds. Never another day."""
    start, end = day_window(request.day or today(settings, now), now, settings)
    if start >= end:
        return []
    return [(request.leagues, start, end)] if request.leagues else [(CORE, start, end), (ALL_COMPETITIONS, start, end)]


def utc_days(start, end):
    day, days = start.astimezone(UTC).date(), []
    while datetime.combine(day, day_start.min, UTC) < end:
        days.append(day)
        day += timedelta(days=1)
    return days


class Research:
    """Provider data for the search, fetched concurrently and within the time budget."""

    def __init__(self, settings, now, start, end, day_start=None):
        self.settings, self.now, self.start, self.end = settings, now, start, end
        self.day_start = day_start or start
        self.diag = Counter()            # pipeline counts for --debug
        self.fetcher = Fetcher(settings.cache_dir, deadline=time.time() + settings.time_budget)
        self.fd = FootballData(self.fetcher, settings.football_data_token)
        self.isports = ISports(self.fetcher, settings.isports_key) if settings.isports_key else None
        self.notes, self.deferred = [], False
        self.fd_fixtures, self.models, self.main, self.days = None, {}, None, {}
        self.odds_api_down = False

    def _note(self, exc):
        self.deferred |= isinstance(exc, Deferred)
        if str(exc) not in self.notes:
            self.notes.append(str(exc))

    def _football_data(self, codes, start, end):
        if self.fd_fixtures is None:
            self.fd_fixtures = self.fd.fixtures(FD_CODES, self.start, self.end)
        for code in sorted({m.league for m in self.fd_fixtures if m.league in codes and start <= m.kickoff < end}):
            if code in self.models:
                continue
            try:
                history = self.fd.history(code, self.now)
                self.models[code] = engine.cached_league(code, history, self.now, self.settings.cache_dir)
            except SourceError as exc:
                self._note(exc)     # this league is priced from the market alone until history arrives

    def _isports(self, days):
        if self.isports is None:
            return
        try:
            if self.main is None:
                self.main = self.isports.all_odds()
        except SourceError as exc:
            self._note(exc)
            self.main = {}
            return
        for day in days:
            if day in self.days:
                continue
            try:
                self.days[day] = self.isports.schedule(day)
            except SourceError as exc:
                self._note(exc)

    def fixtures(self, codes, start, end):
        """All not-started fixtures in scope, from football-data.org and iSports."""
        days = utc_days(start, end)
        with ThreadPoolExecutor(max_workers=2) as pool:
            fd_job = pool.submit(self._football_data, codes, start, end)
            is_job = pool.submit(self._isports, days)
            is_job.result()
            fd_job.result()      # football-data.org errors abort: fixtures must be verifiable
        rows = [r for d in days for r in self.days.get(d, [])]
        fd = [m for m in self.fd_fixtures if m.league in codes and start <= m.kickoff < end]
        linked = odds.link_isports(fd, rows)
        found = linked + odds.discover_isports(rows, codes, start, end, self.main or {})
        self._count(rows, linked, found)
        return found

    def _count(self, rows, linked, found):
        """Overwrite the --debug pipeline counts with the widest scope searched so far."""
        main = self.main or {}
        kick = {id(r): odds._kickoff(r) for r in rows}
        on_day = [r for r in rows if self.day_start <= kick[id(r)] < self.end]
        later = [r for r in on_day if r.get('status') == 0 and kick[id(r)] >= self.start]
        self.diag.clear()
        self.diag.update({
            'schedule rows fetched (UTC days around the date)': len(rows),
            'fixtures on the selected date': len(on_day),
            '  of which started, finished or kicking off within 10 min': len(on_day) - len(later),
            '  of which not started': len(later),
            '  of which with open pre-match odds': sum(str(r['matchId']) in main for r in later),
            'football-data.org fixtures in scope': len(linked),
            '  of which linked to the odds feed': sum(m.isports_id in main for m in linked),
            'fixtures passed to analysis': len(found),
        })

    def quotes(self, fixtures):
        main = self.main or {}
        isports = {m.id: main[m.isports_id] for m in fixtures if m.isports_id in main}
        if self.odds_api_down:
            return isports
        notes = []
        extra = odds.odds_api_quotes(fixtures, self.fetcher, self.settings, notes)
        for note in notes:
            self._note(SourceError(note))
        # A refused key or exhausted credits will not recover within this run.
        self.odds_api_down = any('HTTP 401' in n or 'HTTP 403' in n for n in notes)
        return odds.merge(isports, extra)


def warm_cache_in_background():
    """Fetch what this run had to skip (rate limits), so the next run is fast."""
    try:
        subprocess.Popen([sys.executable, '-m', 'slipbot.prefetch'], cwd=ROOT, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def build_slip(request, settings=None, details=False, out=sys.stdout, debug=False):
    settings = settings or load_settings()
    now = utcnow()
    day = request.day or today(settings, now)
    scopes = stages(request, now, settings)
    if not scopes:
        return f'❌ {day:%d/%m/%Y} has already passed (or its last matches have started).'
    try:
        research = Research(settings, now, scopes[0][1], scopes[0][2],
                            day_start=datetime.combine(day, day_start.min, settings.timezone))
    except SourceError as exc:
        return f'❌ {exc}'
    cal = engine.calibration()
    assessments, picks, searched = {}, [], []
    for codes, start, end in scopes:
        searched.append(codes)
        try:
            fixtures = research.fixtures(codes, start, end)
        except SourceError as exc:
            return f'❌ Could not load fixtures: {exc}'
        cutoff = utcnow() + START_BUFFER     # re-checked: fetching can take a while
        new = [m for m in fixtures if m.id not in assessments and m.kickoff > cutoff
               and m.kickoff.astimezone(settings.timezone).date() == day]
        quotes = research.quotes(new)
        try:
            archive.record(new, quotes, utcnow())
        except OSError:
            pass        # the archive only serves later validation
        for m in new:
            assessments[m.id] = engine.assess(m, research.models.get(m.league), quotes.get(m.id),
                                              utcnow(), settings, cal)
        picks = selection.choose(list(assessments.values()), request.count)
        # Widen to more leagues while the slip is short or still needs speculative picks.
        if len(picks) >= request.count and all(p.tier != 'speculative' for p in picks):
            break
    choose_log = Counter()
    picks = selection.choose(list(assessments.values()), request.count, choose_log)
    if research.deferred:
        warm_cache_in_background()
    if debug:
        print(slip.render_debug(research.diag, list(assessments.values()), picks, choose_log, settings, day),
              file=out)
        print(f'provider notes: {research.notes or "none"}\n', file=out)
    if details:
        print(slip.render_details(list(assessments.values()), picks, settings), file=out)
        print(f'\nsearched {day:%d/%m/%Y}: {["top 5" if c == CORE else "all" if len(c) > 5 else c for c in searched]}; '
              f'{len(assessments)} fixtures assessed\ncalibration: {cal.get("source")}'
              f'\nprovider notes: {research.notes or "none"}\n', file=out)
    if not picks:
        reason = '; '.join(research.notes)
        return (f'❌ No match on {day:%d/%m/%Y} has verified odds above {settings.min_odds:.2f} with enough data'
                + (f' ({reason}).' if reason and not assessments else '.'))
    return slip.render(picks, request.count, settings, day)


def ask(prompt, parse, retry):
    for _ in range(3):
        value = parse(input(prompt))
        if value is not None:
            return value
        print(retry, file=sys.stderr)
    raise ValueError(retry)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Generate a football betting slip for one day.')
    parser.add_argument('request', nargs='*', help='optional, e.g. "8 matches 30/10/2026"; asks otherwise')
    parser.add_argument('--details', action='store_true', help='show the research behind the slip')
    parser.add_argument('--debug', action='store_true', help='show where fixtures and selections are lost')
    args = parser.parse_args(argv)
    settings = load_settings()
    local_today = today(settings)
    try:
        if args.request:
            request = command.parse(' '.join(args.request), local_today)
        else:
            count = ask('How many matches? ', command.parse_count, 'Please enter a number, e.g. 6.')
            day = ask('Date (DD/MM/YYYY, today or tomorrow): ', lambda a: command.parse_date(a, local_today),
                      'Please enter a date like 30/10/2026, "today" or "tomorrow".')
            request = command.Request(count, (), day)
    except (EOFError, KeyboardInterrupt, ValueError):
        return 1
    print('Researching fixtures, statistics and odds…', file=sys.stderr, flush=True)
    print(build_slip(request, settings, args.details, debug=args.debug))
    return 0
