# Betboot — terminal football selection generator

```sh
source .venv/bin/activate
python main.py
```

Three prompts: match count, leagues/countries, and Today/Tomorrow/YYYY-MM-DD.
The default output contains one selection per match, with verified decimal odds of
**1.50 or higher**. Qualified value bets and research-only forecasts are distinguished.
Uncalibrated forecasts never display a win-probability percentage. No account login,
stakes, deposits or bet placement are implemented.

## Search scope

- `ALL`: worldwide competitions available through the configured iSports catalog.
- `England, Spain, Brazil`: all supported competitions in those countries, including lower divisions.
- `Premier League, La Liga`: exactly those named leagues.
- `TOP5`: Premier League, La Liga, Serie A, Bundesliga and Ligue 1.

Names can be mixed, separated with commas. Country names must match the provider's
English catalog names. Times use `RESEARCH_TIMEZONE` (default America/New_York).
Only unstarted fixtures on the exact selected local date qualify. The bot does not
silently widen a named league or change the date to fill a count.

## Data and odds

The existing `.env` and SQLite history are preserved. `FOOTBALL_DATA_TOKEN` supplies
core-league fixtures and history. `ISPORTS_API_KEY` enables global discovery, season
results, context and the alternative 1X2 odds feed, subject to subscription access.
The Odds API supplies additional markets where `ODDS_API_KEY` has available credits.

If The Odds API fails or lacks coverage, iSports current 1X2 prices are checked through
its authorized endpoint. No opening prices or implied double-chance prices are substituted.
Provider identity, kickoff, current status and quote timestamps are checked. Bookmakers
are labelled; their prices are not assumed to be available at 1xBet.

The CLI collects season history for up to `WORLD_HISTORY_LEAGUES` (default 3) new
competitions per run, prioritizing fresh priced fixtures in leagues ahead of cup fixtures.
It also retrieves the previous season when current results do not supply enough
home/away history for the selected teams. Successful
imports are reused; existing training history is retained. Increase this setting locally
if you want broader historical collection and accept longer scans. Endpoint rate limits
can make worldwide research take several minutes. New competitions without enough
results remain unavailable for prediction; discovering fixtures alone is insufficient.

Poisson, Dixon–Coles, Elo and gradient-boosting models are reused where data permits.
The default minimum model estimate is 65% (`SLIP_MIN_PROBABILITY`), a selection preference,
not a demonstrated win rate. Value-qualified bets additionally require calibration,
uncertainty and expected-value checks. Research forecasts remain explicitly labelled.
Only fresh verified prices of at least 1.50 qualify in either path. If too few matches
qualify, the output explains the shortfall instead of inventing fixtures or odds.

## Optional Gemini ranking

Set `GEMINI_API_KEY` and `GEMINI_MODEL` locally in `.env` using your Google AI Studio
project. Google account passwords are never used. Gemini ranks a bounded shortlist of
existing sourced candidates. Its response must contain exactly the supplied IDs;
unknown, repeated or omitted IDs are rejected. Gemini cannot create fixtures, prices
or model probabilities. Final fixture verification follows AI ranking. If Gemini is
unavailable or quota-limited, local statistical ranking continues.

## Details and audit

```sh
python main.py --details
```

This shows alternative market forecasts, data gaps, Gemini status and source URLs.
Normal output remains one prediction per match. Each run stores evidence in SQLite
and private JSON under `artifacts/slips/`. Never treat alternatives for one match as
independent bets. Combining matches increases the chance the whole slip loses.

## Installation and tests

Python 3.12+:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
# First setup only: copy .env.example to .env and configure provider keys.
python main.py
```

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
```

Permitted local Sofascore captures can be imported with
`PYTHONPATH=backend python scripts/import-sofascore.py /path/to/captures/*.json`.
Unknown historical capture times are not backdated. Existing web source remains archived
in `artifacts/legacy-web-backup.tar.gz`; there is no active frontend, dashboard or server.

Provider references: [iSports schedules and season results](https://www.isportsapi.com/en/docs.html?id=41),
[iSports current 1X2 odds](https://www.isportsapi.com/en/docs.html?id=44),
[Gemini generateContent](https://ai.google.dev/api/generate-content).
