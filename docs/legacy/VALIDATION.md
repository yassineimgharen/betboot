# Worldwide search, odds fallback and Gemini ranking — 26 October 2026

- Full regression suite: **99 passed** before the final same-provider corroboration fix.
  Final targeted suite covers terminal, shortlist and iSports odds behavior, including that fix.
- ALL now means provider-catalog worldwide discovery; TOP5 preserves the previous scope.
  Country scopes include lower divisions. Named leagues and exact local-date limits are retained.
- Tests cover country isolation, lower-division selection, core-competition deduplication,
  bounded current-season history imports, timestamp provenance and history reuse.
- Live iSports odds entitlement checks returned HTTP 200 / provider code 0, with 152 and
  143 event records in two responses. The observed schema includes current 1X2 prices
  and change timestamps. The parser and automatic failover have regression coverage.
- Gemini candidate-ranking tests reject invented, repeated and omitted IDs; failed
  or quota-limited generation retains local ranking. No model-generated match facts are accepted.
- A full additional live CLI run was prevented by the existing scan lock. The running
  user's scan was not terminated. Its database activity confirmed broader collection and
  a successful 84-record history import. No six-pick live result is claimed.

# Match count and market alternatives — 25 October 2026

- Full regression suite: **89 passed**, 2.71 seconds.
- Replayed the exact saved failing run `20261025T151307-80952ccc.json` at its original
  timestamp: the new research selection returned **4 of 4** fixtures, each with leaders
  for all six market families. This is an offline regression, not a new live betting slip.
- The original two exclusions had valid official fixtures/history but lacked independent
  context and odds (HTTP 401). Research-only forecasts now disclose those gaps instead
  of silently excluding those fixtures. Value-bet eligibility is unchanged.
- Final live status verification remains mandatory. The replay does not imply those
  fixtures are still upcoming at a later execution time.
- New tests cover four official-only forecasts, absent calibration/odds, six market
  alternatives per fixture and expiration of alternative-market prices before output.

# Research shortlist and Gemini adapter — 25 October 2026

- Full Python suite: **87 passed** in 2.38 seconds.
- Tests exercise a five-forecast shortlist, exclusion of stale/started/wrong-league fixtures,
  five final status verifications, no unvalidated percentages, and Gemini output limited
  to existing recorded gaps. Synthetic tests establish software behavior, not prediction accuracy.
- Live CLI input: 5 matches, Premier League / La Liga / Bundesliga, Today. Result:
  **three research-only forecasts**, zero value-qualified bets. Each displayed fixture
  passed final official-provider status verification. The requested count was not padded.
- Live artifact: `artifacts/slips/20261025T150212-ef4478ab.json`.
- Current fixture batches succeeded: PL 380, PD 380, BL1 306. Started matches were excluded.
- Gemini is **not connected**: no API key/model has been configured. Its adapter has mocked
  request/validation coverage only. A Google account password is not used or stored.

# Terminal release validation — 25 October 2026

This section supersedes the web-stack checks recorded below.

- Complete Python suite: 81 tests passed in 2.45 seconds. After the final abstention
  message change and added finalist-verification regression, all 6 terminal tests passed
  in 1.04 seconds (82 tests now present in the suite).
- Live authorized football-data.org requests returned 380 Premier League and 306 Ligue 1
  season fixtures. Five Premier League and six Ligue 1 fixtures were upcoming on 25 October.
  Sources: https://api.football-data.org/v4/competitions/PL/matches?season=2026 and
  https://api.football-data.org/v4/competitions/FL1/matches?season=2026.
- End-to-end command: `python main.py`, with inputs `2`, `Premier League`, `Today`.
  Exactly three prompts; no web server. Current-season collection succeeded with 380 rows.
  Five upcoming matches were researched with 258–273 observed bookmaker quotes per match.
  The run finished at 2026-10-25 13:44:54 UTC with no collection errors.
- Result: **NO RELIABLE PICKS FOUND**. None passed all calibration, uncertainty, context
  and positive-value checks. This is an abstention, not a validated winning slip.
- Timestamped source evidence and calculations are retained in
  `artifacts/slips/20261025T134454-8b5a2357.json`. Provider credentials are omitted.
- A previous season response failed atomic validation. Cached fixtures were excluded.
  Fresh responses passed on the subsequent check; no schema restriction was relaxed.
- Tests cover local-date scope, started/stale/future observations, duplicate fixtures,
  missing credentials, supported extra-market quotes and final status reverification.
  A postponed finalist is excluded and the next candidate is checked.

Context and imported xG remain coverage limitations. Software tests do not establish
profitability or guarantee a future selection. No bet was placed and no betting account
was accessed.

---

## Historical validation notes (retired web checks included for provenance)

# Validation report — updated 24 October 2026

Tests use synthetic fixtures exclusively to verify software behavior. They do not establish
real-data prediction accuracy, calibration, profitability or access to a provider account.

## Completed checks

- Final backend suite: **47 passed**, 10.19 seconds, on 24 October 2026.
  Includes the final quota-exhaustion regression: remaining matches do not retry an exhausted provider.
- Next.js 16.4.3 production build: compiled, TypeScript checked, routes generated successfully.
- Standalone TypeScript check: passed.
- SQLite migrations 0001 → 0002: passed; Alembic check reports no schema drift.
- Docker Compose configuration: valid.
- Browser integration: **1 end-to-end test passed**, 4.3 seconds, against the standalone
  production dashboard and isolated real FastAPI backend. Covers login, scan submission,
  job completion, NO BET, model performance, paper results, mobile navigation, theme and logout.
  The initial Origin/Host issue and standalone startup warning were fixed before this run.
- Local launcher `scripts/run-local.py --check`: passed; preserved configuration and verified migrations.

Coverage includes probability normalization, analytic totals/BTTS, handicap pushes/half outcomes,
Dixon–Coles/Elo determinism, future-observation exclusion, calibration chronology, repeated
result observations, odds freshness/margin removal, source identity checks, sanitized errors,
atomic ingestion, append-only replay, authentication, persisted predictions, honest no-key
scans, job polling/restart recovery, empty paper results and daylight-saving date windows.

## Environment limits

- Local Python 3.14.7, Node 24 and Chromium. Python 3.12 Docker runtime not verified here.
- Docker socket access denied; PostgreSQL container/runtime verification not completed.
  PostgreSQL CI configuration is included but remote CI has not been run.
- Provider account keys were not supplied. Live research/odds entitlements and actual provider
  schemas have only mocked contract coverage, not a successful authenticated live call.
- No real-data out-of-sample evaluation, validated winning probabilities, ROI or CLV claim.
- API tests and browser localhost binding require execution outside this environment's sandbox.
- Starlette emits a deprecation warning for its httpx test client; tests remain functional.

## Live-data and historical-validation update, 24 October 2026

- Successfully authenticated football-data.org and collected current fixtures/results.
- Ran a complete live scan: **18 matches researched**, zero qualifying recommendations.
  The Odds API and API-Football keys were not configured, so prices and context were not invented.
- Premier League historical replay: 831 results, 675 supported forecasts, 405 calibration
  examples and 270 held-out tests. Away/1X passed the limited diagnostic; totals and BTTS failed.
- Historical-mode candidates now require 200 held-out tests, 50 examples in their calibration
  bin, fresh data and a lower-bound edge; they are explicitly labelled as retrospective.
- Tests verify provenance is not rewritten, target/later outcomes do not enter replay
  training, old/small/failing reports are rejected, and context gates cannot be bypassed.

This supersedes the earlier statement that no provider token or historical test was available.
No bookmaker-odds or API-Football live integration has been authenticated yet. No ROI or
profitability conclusion is supported.

Final checks after historical-mode integration: **52 backend tests passed** (1.84 seconds),
Next.js production build/TypeScript passed, and **1 browser integration test passed**
(13.4 seconds), including the new historical-test button.

## Odds-provider activation, 24 October 2026

- Authenticated The Odds API successfully (HTTP 200); a minimal Premier League request returned 20 events.
- Fixed the observed canonical-name mismatch with explicit football-data.org ID/name to odds-provider aliases for Premier League teams. No fuzzy matching was enabled; league, canonical identity, unique event and kickoff checks remain required.
- Four focused odds/identity tests passed, including wrong IDs, kickoff disagreement, ambiguous matches and provider outcome-name mapping.
- A live Arsenal–Everton analysis saved 185 fresh quotes from 43 distinct bookmakers, covering 1X2, 2.5 totals and supported handicap lines.
- No recommendation was emitted: API-Football context access remains unconfigured. The new odds key was stored only in the local environment file and was not printed.

## Offline Sofascore analytics integration — 24 October 2026

Imported the repaired Arsenal–Liverpool capture without network collection. Dataset
coverage is one unique match, one match with xG, and zero known capture timestamps.
Re-importing the original capture correctly detected the same semantic record and
did not duplicate it. Strict historical replay produced zero usable forecasts and
no eligible xG markets; this is a data limitation, not a successful model backtest.

Added tests cover import idempotence, atomic per-file rejection, revalidation of
capture IDs, missing/false capture timestamps, explicit regulation-time scores,
feature exclusion for future matches, late revisions and the target event,
chronological calibration/holdout separation, refusal to backdate bulk downloads,
stale/changed-dataset calibration rejection, and preservation of existing context
requirements for xG-based recommendation candidates. Synthetic model fixtures are
used only in tests. The full backend suite passed 80 tests after integration.
