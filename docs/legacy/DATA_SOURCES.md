# Provider selection

Reviewed 23 October 2026. Access, price and terms must be rechecked before production use.
No credentials or subscriptions were supplied. No private account access has been verified.

| Provider | Role and availability | Decision/limitation |
| --- | --- | --- |
| football-data.org | Free-tier fixtures and delayed scores; five core leagues supported; 10 calls/minute | Implemented initial adapter. Token required. Historical entitlement and deeper statistics vary by plan. This is distinct from football-data.co.uk. |
| Football-Data.co.uk | Historical CSV results/statistics/odds | Its current data page restricts free use to private individuals and excludes commercial/data-training AI/bot products. No automated importer included. Obtain permission before integration. Page also warns of stale Pinnacle data since July 2025. |
| API-Football | Candidate fixtures, injuries, lineups and statistics API | Future adapter. Verify league/season/endpoint entitlement and quota with the actual account; no access assumed. |
| The Odds API (`the-odds-api.com`) | Candidate timestamped bookmaker prices | Future adapter. Use official v4 API; book/market/region availability must be inspected. Historical snapshots need appropriate entitlement. Do not confuse similarly named services. |
| StatsBomb Open Data | Selected research competitions, event data and lineups | Useful for model research, not assumed to cover ongoing fixtures in four leagues. Observe repository license and attribution requirements. |
| Stats Perform / Opta, Sportradar | Candidate licensed event/context data | No adapter until contract, schema and credentials are supplied. |
| Understat, FBref | Candidate xG/research sources | Not integrated. No automated scraping or assumed commercial reuse rights; establish authorized access first. |
| Official league/club websites | Candidate context corroboration | Not integrated. Record exact source, timestamp and confirmation status if added; do not treat rumors as confirmed injuries. |
| 1xBet | Optional book within an authorized odds feed | Not integrated; no assumed feed support, jurisdiction availability, direct scraping or placement. |

Sources:

- [football-data.org coverage](https://www.football-data.org/coverage)
- [football-data.org plans and delays](https://www.football-data.org/pricing)
- [football-data.org API documentation](https://www.football-data.org/documentation/quickstart)
- [Football-Data.co.uk data usage notice](https://football-data.co.uk/data.php)
- [API-Football documentation](https://www.api-football.com/documentation-v3)
- [The Odds API v4 documentation](https://the-odds-api.com/liveapi/guides/v4/)
- [StatsBomb Open Data terms and attribution](https://github.com/hudl/open-data)

The MVP collects final goals and derives descriptive form. It does not currently collect shots,
xG, injuries, lineups, travel, standings, tactical changes or odds. Missing fields stay missing.
Using an available free API is not a blanket grant of redistribution or commercial rights:
the intended deployment must meet the provider's account terms.

## Provenance and freshness

Every observation records source, external match/team IDs, collection time and provider last
update where present. Collection time is never overwritten. A recent collection does not prove
that a delayed upstream feed reflects recent lineup/schedule changes. The baseline's 24-hour
fixture threshold is a conservative engineering gate, not a statistically validated freshness
policy. Verified odds freshness, book timestamps and event start checks are required before EV
or recommendations are enabled.

## Implemented v2 adapters

API-Football and The Odds API now have optional adapters. API-Football fixture identity and
kickoff are cross-checked against football-data.org; exact names and kickoff within one minute
must match uniquely. Injuries/lineups remain explicitly single-provider reports, and empty
responses do not establish absence. Deep mode also requests standings. The odds adapter collects
1X2, totals and spreads; book timestamps determine freshness. Quotes retain URLs without API
keys. Feed coverage does not establish access to any user's bookmaker account.

Official RSS/Atom URLs may be configured by the server owner only after verifying publisher
permission. Robots disallow/unavailable policy, redirects and invalid feed payloads are skipped.
Relevant headlines are displayed as linked context, not extracted injury or tactical facts.
FBref, Understat, FotMob, Sofascore, WhoScored and Transfermarkt are not scraped or treated as
open licensed APIs. Their presence in the desired source list does not authorize restricted
access or create missing observations.

Premier League odds identity mapping now uses an explicit alias registry in `identity.py`, keyed
by football-data.org team ID and canonical name. Alias matching is restricted to The Odds API
and the Premier League; exact matching remains the default elsewhere. A unique event and
kickoff agreement are still mandatory. Live odds integration was verified on 24 October 2026.
API-Football and other leagues' cross-provider identity mappings are not covered by that test.

## iSports context alternative (24 October 2026)

Set `ISPORTS_API_KEY` to use iSports when `API_FOOTBALL_KEY` is absent. The Live Data
entitlement must include fixtures, injuries and lineups. The adapter uses HTTPS
`/sport/football/schedule/basic`, `/injury`, and `/lineups?isPreview=true`.
Requests are cached per scan and spaced 90 seconds per endpoint within a process;
use one research process per key. The CLI takes an exclusive lock; do not overlap it with standalone collection utilities.
Sources retain collection timestamps and URLs without credentials.

The schedule must uniquely agree on league, home/away identities and kickoff within
one minute, with status not started and a non-neutral venue. Premier League team
aliases use the explicit ID/canonical-name registry; other leagues require exact
names. Empty injury results never imply that everyone is fit. Both starting lists
must contain 11 named, distinct players. Preview responses may be predicted or official;
this adapter does not infer confirmation from their presence.

Normal scans use batch previews (documented next-12-hour coverage). Deep Analyze
Match requests a specific match within the provider's ±3-day window. Missing records
remain explicit gaps. Context is a completeness gate, not a numerical injury adjustment.
The models still lack shot-based xG and independent injury corroboration.

Official references: https://www.isportsapi.com/docs.html?id=41 (schedule),
https://www.isportsapi.com/docs.html?id=64 (injuries),
https://www.isportsapi.com/en/docs.html?id=17 (lineups).
Live verification returned all three context types and 173 odds comparisons for
Manchester United–Bournemouth on 24 October 2026. No price passed the recommendation
criteria. Provider access alone does not establish a statistical betting advantage.

## Global discovery (24 October 2026)

The iSports `/sport/football/league` catalog supplies country/region names, competition
IDs and season labels. Live access returned 2,309 profiles across 176 country/region
labels, including Brazil, Bolivia, France, Russia, Malta and Andorra. This describes
catalog coverage, not guaranteed fixture or betting-market availability everywhere.
Catalog responses are saved with provenance and reused for 24 hours; the endpoint
has a 1,800-second minimum request interval. Documentation:
https://www.isportsapi.com/en/docs.html?id=67

Date-filtered `/schedule/basic` discovery covers the requested UTC dates, respecting
90-second endpoint spacing. Core leagues retain football-data.org identities;
new leagues use provider IDs prefixed IS and source isportsapi.com. Provider country,
league name and season are retained. Invalid identities, inconsistent names, unsupported
season formats, neutral fixtures and extra-time results are excluded. New countries
are discovered from the catalog without code changes.

24 additional competition mappings were checked against both live provider catalogs
for optional The Odds API prices. Team joins remain exact; an available sport key
alone does not verify that a particular match or bookmaker is covered. Bolivia and
many smaller competitions currently have fixture discovery but no mapped odds feed.
Single-provider context is not independent fixture corroboration. For iSports fixtures,
a matched odds-provider event can supply the independent fixture agreement.

Global discovery is not a historical backfill. New leagues generally lack the
historical sample and per-league calibration required for recommendations. Routine
scans archive NO BET before spending odds quota when the baseline history is missing;
Deep Analyze Match can still retrieve available context. The 90% estimate target and
all evidence/value gates remain unchanged. No global 90% win-rate claim is made.

## Sofascore access review — 24 October 2026

Reviewed the published terms and robots rules before implementing the requested
scraping integration. Terms sections 2.8–2.9 restrict database extraction and scraping
without explicit consent, subject to applicable legal exceptions. Robots rules also
exclude specified paths; paths absent from robots.txt do not establish reuse permission.

No Sofascore scraper was enabled. This follows the project's requirement to use
permitted access methods and respect website restrictions. A permission-backed
integration needs the permitted URLs/data fields, retention/reuse scope, and request
limits; these must not be inferred from an accessible undocumented endpoint. Existing
licensed data integrations remain active. No Sofascore statistics have been imported
or represented as model inputs.

Sources checked:
- https://www.sofascore.com/terms-and-conditions
- https://www.sofascore.com/hi/terms-and-conditions (indexed English terms text)
- https://www.sofascore.com/robots.txt
