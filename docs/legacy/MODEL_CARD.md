# Poisson shrinkage baseline v1

Status: **research only, uncalibrated, no real-data performance claim**.

## Estimation

Within the fixture's source and league, use final scores from the preceding 730 days, collected
by the prediction cutoff. Require 50 league matches and at least 5 matches in the relevant
home/away venue for each team. Each team's latest available result must be within 60 days.
Require an upcoming `TIMED` fixture collected within 24 hours. The configuration is persisted
with each prediction; these thresholds are provisional and need out-of-sample selection.

For observation age `d` days, weight `w = 0.5^(d/180)`. Compute weighted league home/away
goal means `mu_h`, `mu_a`. Shrink each relevant team scoring/conceding rate toward its
corresponding league mean using 8 pseudo-matches:

`rate = (sum(w * goals) + 8 * league_mean) / (sum(w) + 8)`

Then:

`lambda_home = home_home_scoring_rate * away_away_conceding_rate / mu_h`

`lambda_away = away_away_scoring_rate * home_home_conceding_rate / mu_a`

Generate independent Poisson goal distributions. Extend support until each omitted tail is
below `1e-12`, then normalize. Sum scoreline mass for each market. Double chance outcomes
overlap, so they are not a mutually exclusive probability vector. Fair odds are reciprocal
probabilities, without bookmaker margin. Asian handicap is not implemented.

The training hash includes normalized input observations. Model code version, full config,
training match IDs/cutoffs, fixture snapshot and calculated output are stored together.

## Limitations and intended use

No fitted Dixon-Coles correlation, opponent-strength regression, xG, Elo, lineup, injury,
shot-quality, travel, tactical, rest or match-importance inputs. Historical venue samples can
be biased by opponents and promoted teams. Goal independence and stationary team strength
are approximations. No bootstrap interval, calibration model, or quality scoring rubric has
been validated; these fields are null. Goal intensities must not be presented as measured xG.

Descriptive form is not opponent-adjusted. Head-to-head is not used. No confidence label or
stake recommendation is generated. A complete form window is not evidence of prediction quality.

## Evaluation plan

Freeze data and code hashes; construct chronological train/calibration/test windows. Fit
features, hyperparameters and calibrator exclusively within training/calibration windows.
Refit by rolling origin, with a final untouched period. A retroactive results download cannot
be used to claim strict prospective knowledge: only begin strict replay where archived
observation timestamps exist. Retrospective exploratory evaluation must be separately labeled,
with publication-lag assumptions and data limitations recorded.

Measure multiclass Brier score, clipped multiclass log loss, reliability bins with sample
counts and uncertainty, and compare to league-frequency and margin-adjusted bookmaker
baselines on matched samples. Assess by league, season, probability range and market.
Choose calibrators on earlier validation windows; preserve coherent outcome probabilities.

Betting evaluation additionally requires historical pre-match odds that were actually available
at decision time. Reject stale/in-play/unmatched prices, account for settlement/push/void rules,
and report skipped opportunities. Track turnover, net P&L, ROI/yield with explicit denominators,
win rate, maximum peak-to-trough drawdown, and CLV against a defined same-market closing price.
Report transaction/slippage assumptions and correlation-aware uncertainty. No such evaluation
has been completed in milestone 1.

## Research ensemble v2

The current `research-ensemble-v2` layer adds Elo (initial 1500, K=20, +65 home advantage),
a historical draw-rate prior, Dixon–Coles rho fitted on chronological low-score observations,
and scikit-learn gradient boosting (80 trees, depth 2, learning rate .04, seed 17) when there are at
least 120 complete chronological feature examples and all three outcomes. Features are recent
team scoring/conceding means and Elo difference. Historical feature construction precedes
incorporation of that match's result. No target-match result enters training.

Dixon–Coles v2 fits the low-score correlation using historical one-step goal-rate approximations;
it is not a full jointly optimized attack/defence maximum-likelihood implementation. Positive
score-cell mass is checked and the distribution normalized. Asian handicap quarter-lines split
into two adjacent half-goal lines, with full/half wins, losses and pushes. Its probabilities
remain uncalibrated and cannot generate recommendations.

The ensemble averages available component probabilities with fixed equal weights. Goal markets
use Poisson and Dixon–Coles; 1X2 also uses Elo and gradient boosting when available. Derived double-chance
probabilities are recomputed from ensemble 1X2. No ensemble weights are fitted on the test set.
Individual component outputs are uncalibrated research estimates.

### Prospective validation and recommendation gate

`evaluation.py` consumes archived v2 forecasts created before the actual observed kickoff and
subsequently scored. It keeps the first eligible forecast per fixture. For each league/market,
fit isotonic calibration on at least 60 earlier forecasts whose results were known at the
current test forecast time. Later result revisions cannot enter earlier calibration labels.
An expanding-window out-of-sample sequence must contain at least 40 scored forecasts. It must
beat its expanding frequency baseline in Brier score and have five-bin ECE <= .08.

For live inference, refit the calibrator on all currently scored eligible forecasts. A selected
calibration bin must have at least 20 out-of-sample observations. Its 95% Wilson interval
estimates the **bin's observed outcome frequency**, not parameter uncertainty for an individual
match. This interval is conditional on the binning and historical sample; it does not account
for all temporal/team dependence or model-selection uncertainty. Markets calibrated separately
are not asserted jointly coherent; only one selection per fixture can be recommended.

Recommendations additionally require lower-bin-bound EV >= .02, interval width <= .35,
complete current inputs and fresh quotes. Categories are descriptive: Conservative (p>=.65
and width<=.2), Balanced (p>=.45), otherwise Aggressive. These heuristic policy thresholds are
versioned engineering rules, not empirically optimized guarantees. A high point probability
is never labeled safe. The completeness score is a transparent weighted checklist, not a
calibrated reliability probability.

No real-data performance evidence or passing real-market model was available at delivery.

## Historical route added 24 October 2026

`historical-poisson-v1` replays the goal-only baseline on latest downloaded final results.
The real collection timestamp is never changed or inserted into the live prediction table.
The simulation assumes final results are available six hours after kickoff, uses a one-hour
pre-match decision time and excludes target/later outcomes from training. It uses the earlier
60% of replayable forecasts for isotonic calibration and the later 40% for testing, keeping
same-time matches on the same side and removing results unavailable at the test boundary.
This is retrospective evidence and can be affected by unreconstructed result/schedule corrections.

A fixed, labelled historical calibrator is now an alternative to the prospective-only gate
described above. It requires 200+ final-test forecasts, 50+ observations in the applicable bin,
a passing Brier/frequency-baseline and ECE diagnostic, a report no older than seven days, and
all existing live-data checks. Raw probabilities come from the **same replay Poisson estimator**,
not the different ensemble. The mode and interval provenance are displayed on each candidate.
No historical ROI or bookmaker-price validation is claimed.

Initial Premier League replay: 831 final results, 675 supported forecasts, 405 calibration
forecasts and 270 held-out tests. Away and its complementary 1X market passed the limited
diagnostic. Totals and BTTS did not outperform the frequency baseline after calibration.
The earlier Arsenal–Everton Under 2.5 research suggestion is not supported by this diagnostic.

## Imported xG model v1

`imported-xg-poisson-v1` is a separate experimental model over locally supplied
Sofascore captures. The dataset is stored as immutable `sofa_observation` artifacts,
with semantic fingerprints, file hashes, capture/import availability and revisions.
Unknown collection times use import time, never kickoff time. This dataset does not
replace existing source-specific observations or model calibration.

At each prediction cutoff, resolve the latest available version per event, exclude
the target and future fixtures, and retain up to 730 days of prior xG observations.
Require at least 50 league matches, 5 matches at each target venue and results for
both teams within 60 days. For each side, shrink the mean xG of its latest 20 venue
matches by eight league-average observations. Expected home goals = home attacking
xG × away conceded xG / league home xG mean; the away calculation is symmetric.
An independent Poisson score distribution supplies market probabilities. Recent
10-match goals, xG and xGA are descriptive features shown separately. No injury
adjustment or cross-provider xG scale conversion is assumed.

Calibration uses chronological replay at kickoff minus one hour with strictly
available historical inputs. First 60% of replayable fixtures are the calibration
period; fixtures at the boundary timestamp remain together. Training labels must
be available before the first test cutoff. The calibrator is frozen for the final
40%; no test outcomes refit it. Targets use recorded kickoff times, so this is not
an archive of prospective predictions or historical schedule revisions.

Live eligibility requires 200 held-out tests, 50 observations in the applicable
Wilson frequency bin, Brier improvement over the calibration-period frequency
baseline, ECE <= .08, a matching dataset fingerprint and a report at most seven days
old. The estimate and the lower cohort bound must both meet the existing positive-EV
criteria, and the user probability target, odds freshness, fixture corroboration,
injuries/lineups and existing baseline data gates must pass. A cohort interval is not
an individual-match confidence guarantee. xG is a fallback only when the existing
model has no calibrated estimate for the market. No profitability has been established.

Cross-provider fixture linkage uses the locally verified Premier League competition
ID or unique exact competition name/country agreement, and unique exact/explicit-alias
team identities. Ambiguous mappings abstain. These mappings do not establish that
third-party data may be collected or redistributed.
