# Football betting slip bot

```sh
source .venv/bin/activate
python main.py
How many matches? 8
Date (DD/MM/YYYY, today or tomorrow): 30/10/2026
```

Or in one go: `python main.py "8 matches 30/10/2026"` (without a date, today is used).

The bot analyses **only the matches on the chosen date**, judged in `Africa/Casablanca` time
(`SLIP_TIMEZONE`). Kickoff times are shown in that timezone too. It never looks at another day, never
offers a match that has started or starts within 10 minutes, and returns fewer selections than requested
if the day has fewer suitable matches. It never logs in to a bookmaker, places bets or suggests stakes.

Date answers: `today`, `tomorrow` (also `demain`, `ghedda`, `lyoum`), `30/10/2026`, `30-10-2026`,
`2026-10-30`; an empty answer means today. Free-text requests may also name leagues
(`"5 matches serie a tomorrow"`).

`--debug` prints where fixtures and selections are lost:

- fixtures fetched, on the date, started, and with odds;
- football-data.org fixtures linked to the odds feed;
- markets quoted, and selections priced, above 1.30, model-checked and validated;
- every rejection reason with its count;
- eligible matches per competition.

`--details` prints the research behind the slip: for every fixture, how many markets were priced, and the
top candidates with their model, market and calibrated probabilities, uncertainty, score, tier and EV.

## Markets

Every market that has verified prices for a match is evaluated independently. Each match then gets its
best-scored selection, whatever the market; no market is preferred and none is forced for variety.

| Market | Probability from | Prices from |
| --- | --- | --- |
| 1X2, double chance | Dixon–Coles + Elo blend, pooled with the bookmaker consensus | iSports (1X2); The Odds API (double chance) |
| Over/Under 0.5, 1.5, 2.5, 3.5, 4.5 | goals-tuned Dixon–Coles score distribution + consensus | iSports main line per book; The Odds API alternate totals |
| BTTS Yes/No | joint score distribution + consensus | The Odds API |
| Asian handicap, −2.5 … +2.5 incl. quarter lines | joint score distribution with exact settlement + consensus | iSports main line per book; The Odds API spreads |

Asian handicaps settle properly:

- whole lines (e.g. −1.0) can push, so the stake comes back;
- quarter lines (e.g. −0.75) split the stake across the two neighbouring lines, so they can half-win or
  half-lose.

The probability shown is the chance of winning when the stake is not returned. The slip adds
"stake back X%" where a push is possible, and EV counts the returned stake as earning nothing.

**Not offered: corners and cards.**

- **Prices:** iSports sells corner prices only in its "Odds Pro" product (the current key does not
  include them). The Odds API lists corner and card totals, but it has no credits.
- **Model:** no source on the current keys has the historical corner/card statistics needed to build
  and validate a model.

Activating them needs both a price feed and a historical statistics feed: for example iSports Odds Pro
plus its Historical Database, or API-Football (fixture statistics and odds; key `API_FOOTBALL_KEY`). The
bot does not invent probabilities for them.

Over 0.5–4.5, Under 1.5–4.5 and every handicap line come from iSports `odds/all`, which carries
alternate lines and not just each bookmaker's main one. Double chance and BTTS are not part of the iSports
plan; they arrive through The Odds API once its credits are available.

## Coverage and search

| Competitions | Fixtures verified by | Probability from |
| --- | --- | --- |
| Premier League, La Liga, Serie A, Bundesliga, Ligue 1 (searched first) | football-data.org | models, cross-checked with the bookmaker consensus |
| Primeira Liga, Eredivisie, Championship | football-data.org | same |
| Belgian Pro League, Turkish Süper Lig, Champions League, Europa League, Conference League, Scottish Premiership, Austrian Bundesliga, Swiss Super League, Greek Super League, Danish Superliga | iSports schedule + live odds feed | bookmaker consensus only, with a larger uncertainty |

**Every other competition** iSports prices on that day is included too: cups (Copa del Rey,
DFB-Pokal, KNVB Beker...), second tiers, and leagues in other countries. These have no model. They are
priced from the bookmaker consensus, calibrated on its own record outside the named competitions (see
docs/VALIDATION.md), and carry an extra uncertainty penalty.

On the chosen day the bot looks at the top five leagues first. It widens to every competition if that
gives fewer selections than requested, or the slip would need speculative picks.

## Selection rules

**Hard rules** (never relaxed):

- the fixture exists and has not started: football-data.org for its leagues, otherwise open pre-match
  quotes in the iSports odds feed, and kickoff more than 10 minutes away;
- the shown decimal odds are verified and **strictly greater than 1.30** (`SLIP_MIN_ODDS`): at least
  3 bookmakers quote the market, the shown price is within 15% of the median and changed in the last 48 h;
- there is enough data for an estimate (a calibrated bookmaker consensus, and a fitted model where the
  league has history);
- one selection per match, no fixture twice (even when it comes from two providers), no team twice.

**Everything else is ranking.** Each candidate's score is its *conservative probability* (the calibrated
estimate minus its uncertainty) plus a small expected-value term (`0.5 × EV`, capped at ±15%). The
uncertainty combines:

- the model's parameter uncertainty;
- disagreement between bookmakers;
- half the model/market gap;
- a penalty for markets without out-of-sample validation and for fixtures with no model.

A 72% favourite at a fair price therefore outranks a 45% shot with a large EV, and long shots come last.
Internally candidates are tiered "high" (≥70% after uncertainty), "medium" (≥55%) or "speculative"
(`--details` shows this).

The slip shows each pick's estimated probability and the estimated probability that all of them win. That
is the product of the pick probabilities, which assumes independence (reasonable across different matches
with different teams). Ten 66% picks all win only about 2% of the time: a longer slip is much less likely
to pay out.

**Why earlier versions misbehaved.** The first version required positive EV and ranked by it, so a few
points of probability error on a 5.00 underdog looked like a large edge and long shots won the ranking.
The second version fixed that with a hard 70% floor, plus a hard model/market agreement cut. But it only
searched the top five leagues for 72 hours, which that week meant 10 Serie A matches. Almost everything
failed the floor, so a request for 6 returned 2. Now the floor is gone (probability drives the ranking),
and the search widens until it has enough good candidates.

## How it works

1. **Fixtures and history:** [football-data.org](https://www.football-data.org/) (free tier, `FOOTBALL_DATA_TOKEN`).
   It covers this season and the two before it, about 840 league matches per league.
2. **Models:**
   - Dixon–Coles (time-weighted, ridge-regularised Poisson with the low-score correction), fitted twice:
     once tuned for results, once with much stronger shrinkage for goals.
   - Elo with home advantage and goal-difference weighting, mapped to 1X2 by an ordered logit.
   - 1X2 uses a validated blend of the two.
   - Prediction uncertainty comes from 400 draws of the Dixon–Coles parameter covariance (inverse Hessian).
3. **Odds:**
   - iSports API `odds/all` (`ISPORTS_API_KEY`): one request returns every open pre-match line (1X2, all
     goal lines, all Asian handicap lines) of about 18 bookmakers, including 1xBet and Pinnacle, for every
     fixture in the next 14 days.
   - The Odds API (`ODDS_API_KEY`) is merged in when it has credits.
4. **Market blend:** the model is pooled with the margin-free median of all bookmakers
   (`p ∝ model^w · market^(1−w)`). The weight `w` is fitted out-of-sample by `python -m slipbot.validate`
   and stored in `slipbot/calibration.json`. Markets are efficient, so `w` is small; the bot mainly picks
   where the model and the best available price both disagree with the consensus.

Validation results and their limits are in [docs/VALIDATION.md](docs/VALIDATION.md). Re-run
`python -m slipbot.validate` occasionally (it needs about 35 minutes the first time because of the
odds-archive rate limit).

## Speed

A run with warm caches takes 2–5 seconds. The two providers are fetched in parallel, fitted models are
cached in `.cache/` and refitted only when new results arrive, and no validation runs during slip generation.

iSports allows one schedule request per minute. On the first run of the day, a run therefore waits at
most `SLIP_TIME_BUDGET` seconds, uses what it has (older cached schedules are used when a refresh would
exceed the budget), and starts `python -m slipbot.prefetch` in the background to fetch the rest. That
process only downloads public schedule/result data into `.cache/`, and one instance runs at a time.

## Data limits (be aware)

- **Markets actually available:**
  - The current iSports plan has 1X2 from many books, but over/under only on each book's *main* line,
    so usually only O/U 2.5 (occasionally 3.5).
  - Double chance, BTTS and Over 1.5 / Under 3.5 come from The Odds API's per-event "additional markets"
    (about 3 credits per event, up to `SLIP_EXTRA_MARKET_EVENTS`, default 10 per run). They appear once
    that key has credits again.
  - These are the markets where 70%+ selections above 1.30 are most common (e.g. 1X on a home favourite),
    so expect more and better picks then.
  - The bot never derives a price from other markets.
- **Goals markets** use the market consensus alone (model weight 0) until enough real quotes have been
  recorded to validate the model against the market. Each run appends what it sees to
  `data/odds-snapshots.jsonl`, and `validate` fits that weight once 200 observations exist.
- **Not modelled:** xG, injuries, suspensions, lineups, rest days. No source on the current keys provides
  them legally. Understat, FBref, FotMob, Sofascore and Transfermarkt are not scraped. Football-Data.co.uk
  excludes automated bot/AI use in its terms. The market price already reflects such news, which is one
  reason the model is pooled with it.
- **The Odds API** key has used its 500 free monthly credits; the bot skips it until they reset. A paid
  plan (or API-Football for injuries/lineups) would widen market coverage and context.

## Settings (`.env`)

| Key | Default | |
| --- | --- | --- |
| `FOOTBALL_DATA_TOKEN` | required | fixtures, results |
| `ISPORTS_API_KEY` | required | odds |
| `ODDS_API_KEY` | optional | extra odds |
| `SLIP_MIN_ODDS` | 1.30 | strict lower bound |
| `SLIP_EXTRA_MARKET_EVENTS` | 10 | events per run for double chance/BTTS/alt. totals (Odds API credits) |
| `SLIP_BOOKMAKER` | 1xBet | preferred price source |
| `SLIP_TIMEZONE` | Africa/Casablanca | decides a match's date; times are shown in it |
| `SLIP_MAX_DAYS` | 7 | days ahead the background warm-up caches schedules for |
| `SLIP_TIME_BUDGET` | 45 | seconds a run may wait on provider rate limits |
| `SLIP_MAX_ODDS_AGE_HOURS` | 48 | max time since a price last changed |

## Development

```sh
python -m pip install -r requirements.txt pytest
python -m pytest
```

Layout: `slipbot/command.py` (request parsing), `sources/` (providers), `models/` (Dixon–Coles, Elo),
`quotes.py`/`odds.py` (verification and consensus), `engine.py` (probabilities and candidates),
`selection.py`, `slip.py`, `validate.py`. API responses are cached in `.cache/`.

The previous app (`backend/`, `python legacy_main.py`) and its docs (`docs/legacy/`) are unchanged.
