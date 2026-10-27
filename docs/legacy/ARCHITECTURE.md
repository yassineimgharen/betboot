# Terminal architecture

`main.py` loads the local configuration and calls `betboot.terminal`.
The user answers three questions: match count, leagues and local date.
One process performs research and prints a compact manual slip. No server is started.

1. `providers/football_data.py` fetches fixtures and completed results for the selected leagues.
2. `collection.py` validates complete batches and records observations in local SQLite.
3. `research.py` obtains matching bookmaker quotes and context through configured APIs.
4. `pipeline.py`, `analytics.py` and `models/` calculate forecasts, calibration and value.
5. `shortlist.py` builds explicitly labelled research forecasts when value bets are insufficient.
   Uncalibrated forecasts never display probability percentages; absent context stays a gap.
   Official-only fixtures may have research forecasts, but cannot bypass the stricter value-bet gates.
   Default output shows one selection per match. With --details, six market leaders
   are shown as alternatives.
6. `terminal.py` refreshes prices, verifies finalist fixture status, enforces the date/league
   boundaries, and selects at most one market per match with no repeated teams.
7. Optional `gemini.py` sends sourced match/market candidates and recorded gaps to the Gemini API.
   Only a permutation of existing candidate IDs is accepted; AI output cannot create facts or picks.
8. Timestamped evidence is saved to SQLite and a private JSON file in `artifacts/slips/`.

Provider identities and explicit authoritative names are used for matching; ambiguous
matches are rejected. A failed refresh cannot qualify old cached fixtures for a slip.
Observed bookmaker prices are labelled with their source; availability at 1xBet is not assumed.

Historical replay is labelled separately from prospective validation. New model versions
cannot inherit calibration from older models. Missing inputs remain gaps. The bot may return
fewer selections than requested or NO RELIABLE PICKS FOUND.

`.env` supplies credentials and the research timezone. SQLite stores immutable fixture
observations, predictions and research artifacts. Historical imports preserve collection times;
they are never silently backdated to manufacture prospective evidence.

There is no frontend, API service, scheduler, betting-account connection or stake sizing.
The old web implementation and design documents are archived under `artifacts/`.

Worldwide/country discovery uses the iSports catalog and date-bounded schedules; named
league scopes remain exact. Core identities are deduplicated across providers, including
Ligue 1. Bounded, reusable current-season history imports prioritize leagues present in
the odds feed. Alternative iSports odds use current 1X2 prices, provider change timestamps
and exact schedule identities. Same-provider fixtures and prices are not independent
corroboration. Final iSports fixture verification is batched by match ID.

History bootstrap now checks league and per-team venue coverage, rather than treating
50 or 100 league results alone as sufficient. It imports the previous season when
needed, preserves real collection times, and rejects IDs that collide across seasons.
Fresh priced regular-league fixtures take priority over priced cups or unpriced games.
Explicit canonical-ID/name aliases cover the observed PSG, Lyon and Sevilla feed labels.
No fuzzy identity matching or relaxed price-age rule was introduced.
