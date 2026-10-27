# Validation

## Update (27 October 2026): bookmaker consensus in every competition

The bot now also prices cups, second tiers and other countries' leagues from the bookmaker consensus.
To check that this is safe, the iSports odds archive (pre-kickoff 1X2 prices, all competitions, about
one month) was joined by match id to iSports final scores.

| Group | Matches | Consensus rated ≥65%: predicted → observed (n) |
| --- | --- | --- |
| Named competitions, 1X2 | 470 | 72.9% → 77.9% (104) |
| Named competitions, double chance | 470 | 77.2% → 80.9% (915) |
| Other competitions, 1X2 | 13,445 | 74.4% → 78.7% (2,201) |
| Other competitions, double chance | 13,445 | 76.4% → 77.5% (26,080) |

Outside the named competitions the consensus is calibrated to slightly cautious in the high-probability
region, so no correction is subtracted. The extra 0.03 uncertainty used for those competitions is
therefore conservative. Goals and handicap markets in those competitions have no archived prices yet,
so they rely on the same consensus method with that penalty.

## Update (27 October 2026): Asian handicap and all goal lines

The walk-forward test (2,733 matches, eight leagues) now also scores:

- Over/Under 0.5 and 4.5;
- every Asian handicap line from −2.5 to +2.5, both sides, using the joint Dixon–Coles score
  distribution.

Handicap outcomes are the fraction of the decided stake won: a quarter line can half-win, and pushes are
excluded. The base rate comes from the same settlement on the league's recent matches.

| Market | Model | Base rate | Rated ≥65%: predicted → observed (n) |
| --- | --- | --- | --- |
| Over/Under 0.5 | 0.2267 | 0.2282 | 93.2% → 94.0% (2,733) |
| Over/Under 4.5 | 0.3884 | 0.3933 | 85.4% → 86.6% (2,720) |
| Asian handicap (all lines) | 0.4321 | 0.4751 | 85.2% → 85.7% (44,100) |

The handicap high band is dominated by wide lines (e.g. +2.5), which bookmakers rarely offer as a main
line. It shows the score distribution is calibrated, not that main-line handicap picks are easy.
Corners and cards are not validated or offered: no historical data for them is available on the
current keys.

## Update (27 October 2026): eight leagues, ranking replay

`python -m slipbot.validate` now covers all eight modelled leagues (the top five plus the Primeira Liga,
the Eredivisie and the Championship). That is 2,733 walk-forward matches.

- **1X2 blend:** log loss 1.0030 vs 1.0766 for base rates.
- **Selections rated ≥65%:**
  - double chance: predicted 75.5%, observed 75.5% (n=5,326);
  - Over 1.5: predicted 75.7%, observed 77.5% (2,705);
  - Under 3.5: predicted 73.2%, observed 73.0% (2,122);
  - 1X2: predicted 73.2%, observed 77.5% (329).
- **BTTS:** now marginally beats base rates (0.6845 vs 0.6852), so it is flagged validated. Its ≥65% band
  is overconfident (67.0% predicted, 61.8% observed, n=89), and the bot subtracts that gap from the model.
- **Consensus on 269 archived matches:** 1X2 ≥65% predicted 71.3%, observed 67.6% (n=37; that gap is
  subtracted); double chance predicted 76.1%, observed 79.0% (n=528).
- **Pooling weight** for the model against the market: 0.05 (90% interval 0.00–0.85).
- **Replay of the current ranking** (best-scored 1X2 pick per archived match):
  - medium tier: 43 picks, predicted 63.8%, won 58.1%;
  - speculative tier: 149 picks, predicted 42.8%, won 48.3%.

  These are small samples, indicative only.

The sections below are the earlier five-league results, kept for reference.

# Validation (27 October 2026, per-market update)

Reproduce with `python -m slipbot.validate`, which also rewrites `slipbot/calibration.json`.

## 1. Models vs base rates: walk-forward, 2025/26 season

There are 1,647 league matches across the five leagues. Models were refitted every week on earlier matches
only. Matches whose teams had fewer than 8 league games in the previous year were excluded, as in the app.

| 1X2 model | Log loss | Brier |
| --- | --- | --- |
| Base rates (recent league frequencies) | 1.0725 | 0.6486 |
| Elo (ordered logit) | 1.0078 | 0.6015 |
| Dixon–Coles (ridge 20, half-life 500 d) | 1.0052 | 0.6000 |
| **Blend, Dixon–Coles weight 0.5 (deployed)** | **1.0016** | **0.5977** |

Reliability of the blended home-win probability:

| Predicted band | Predicted | Observed | n |
| --- | --- | --- | --- |
| 0–20% | 15.8% | 16.3% | 135 |
| 20–35% | 28.4% | 30.0% | 443 |
| 35–50% | 42.3% | 44.7% | 548 |
| 50–65% | 56.7% | 55.2% | 359 |
| 65–80% | 70.7% | 76.4% | 140 |
| 80–100% | 83.9% | 86.4% | 22 |

Goals markets use a separate Dixon–Coles fit with ridge 60 and a 500-day half-life. Binary log loss:

| Market | Model | Base rate |
| --- | --- | --- |
| Over 1.5 | 0.5356 | 0.5401 |
| Over 2.5 | 0.6831 | 0.6855 |
| Over 3.5 | 0.5922 | 0.5973 |

**Tuning note.** The first version used weak regularisation (ridge 1, 240 days). Its goals models were
*worse* than base rates and badly overconfident: when it predicted 30% for Over 2.5, the observed rate
was 49%. Ridge and half-life were then chosen on Aug–Dec 2025 and confirmed on Jan–Jun 2026:

| Over 2.5 on Jan–Jun 2026 | Log loss |
| --- | --- |
| Ridge 60 | 0.6813 |
| Ridge 1 | 0.6915 |
| Base rate | 0.6848 |

The full-season figures above therefore include the half of the season used for tuning. The goals
model's edge over base rates is small.

## 1b. Every market, and the 70%+ region that matters for picks

Same walk-forward test (1,647 matches). Log loss is binary per selection. The last column covers
selections the model rated 65% or higher.

| Market | Model | Base rate | Skill | Rated ≥65%: predicted → observed (n) |
| --- | --- | --- | --- | --- |
| 1X2 | 0.5848 | 0.6232 | yes | 72.2% → 76.2% (206) |
| Double chance | 0.5848 | 0.6232 | yes | 75.8% → 75.9% (3,201) |
| Over/Under 1.5 | 0.5356 | 0.5401 | yes | 76.4% → 76.9% (1,630) |
| Over/Under 2.5 | 0.6831 | 0.6855 | yes | 68.6% → 75.6% (78) |
| Over/Under 3.5 | 0.5922 | 0.5973 | yes | 72.7% → 73.6% (1,225) |
| **BTTS** | 0.6865 | 0.6860 | **no** | 66.8% → 57.6% (66), overconfident |

BTTS is therefore marked not validated and is never selected. In the 70%+ region the other markets are
calibrated or slightly underconfident. Where a high band is overconfident (predicted above observed, with
n ≥ 30), the bot subtracts the gap. It never adds to a probability.

The bookmaker consensus was checked on 220 archived matches from eight leagues (the top five plus the
Eredivisie, Primeira Liga and Brasileirão):

| Consensus rated ≥65% | Predicted | Observed | n |
| --- | --- | --- | --- |
| 1X2 | 71.7% | 70.6% | 34 |
| Double chance (from 1X2) | 76.5% | 79.6% | 431 |

## 2. Model vs bookmaker market (1X2)

This uses 104 matches from the current season with archived pre-kickoff prices (iSports
`odds/european/all`, about one month retained; quotes changed after kickoff are excluded).

| | Log loss |
| --- | --- |
| Market consensus (margin-free median of all books) | 0.9435 |
| Model alone | 0.9625 |
| Best log-linear pool | model weight **0.0** (90% bootstrap interval 0.00–0.70) |

The market is more accurate than the model, as expected: it prices injuries, lineups and news that the
model does not see. The deployed model weight is therefore 0, so the estimated probability equals the
market consensus, calibrated as in section 1b. A pick needs that estimate to be at least 70%, and the
independent model must agree within 10 points (and reach at least 65%). The bot ranks by the lower of the
two estimates, minus one standard deviation.

A replay of the current selection rule on this archive (1X2 only, since the archive has no other markets)
found 1 qualifying pick, a 70.1% favourite at 1.33, which lost. One pick is no evidence either way. The
calibration tables above are the meaningful evidence that a "70%" estimate wins about 70% of the time.

## 3. Goals markets vs market

No historical over/under odds are available on the current plans. Each run of the bot records the
quotes it sees in `data/odds-snapshots.jsonl`, and `validate` fits the goals pooling weight once 200
observations exist. Until then the goals model weight is 0.

## What this does not show

- That the bot wins money. No test here has the sample size or out-of-sample odds history to show a
  betting edge.
- Anything about accumulators. Combining selections multiplies the risk: five 60% picks all win only
  about 8% of the time.
