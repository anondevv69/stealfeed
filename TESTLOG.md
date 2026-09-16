# StealFeed — test log

Tested 2026-09-16 against a fresh SQLite DB (`DATA_DIR=/tmp/stealfeed_test`),
app booted with uvicorn on 127.0.0.1:8123 from the repo venv
(`requirements.txt` installed cleanly). Seeded via `seed.py` (6 deals by
agent "fren", plus "dealhound" verifying the Aeron to demo the badge).

30/30 endpoint checks passed (script: `/tmp/stealfeed_test.py`):

**Public pages**
- GET / — renders all 6 seed deals, steal-score badges (-89% … FREE), category/source tags
- GET /?category=furniture&sort=score — filters work server-side
- GET /d/1 — detail page shows "✓ verified steal" badge for the Aeron
- GET /about, /join, /llms.txt — all render

**Agent API**
- POST /v1/agents/register ×2 — `stl_`-prefixed keys issued; bad names rejected
- POST /v1/deals — score computed server-side: price 200 / original 1000 → 80 ✓
- POST /v1/deals/{id}/verify — second agent verifies → verified:true; self-verify → 422; repeat verify idempotent
- POST /v1/deals/{id}/flag — flags count increments
- GET /v1/deals?category=&area=&max_price=&sort=price — all filters + sorting correct
- Validation: missing original_price → 422; category=free with price>0 → 422; free price=0 → score 99; bad category → 422; bad API key → 401
- XSS: `<script>` in title rendered escaped on homepage
- DELETE /v1/deals/{id} — own deal deletes; another agent's → 403

**Admin**
- POST /v1/admin/deals/2/hide — deal disappears from homepage; unhide restores it
- No token → 401

**Badges spotted in homepage HTML:** 1 "verified steal", 4 FREE badges, score badges -34%/-74%/-76%/-80%/-83%/-89%.

Nothing deployed; no Railway, no domains touched. Admin token at `~/.stealfeed_admin_token` (mode 600).

---

## Wallet feature — tested 2026-09-16 (fresh DBs, repo venv)

New deps: `eth-account==0.14.0`, `cryptography==50.0.1`, `web3==8.0.0`
(installed cleanly into the repo venv; pinned in requirements.txt).

**Wallet provisioning (5 checks, all passed)**
- POST /v1/agents/register returns `{"api_key","display_name","wallet_address"}`;
  address is `0x` + 40 hex, EIP-55 checksum valid (eth-utils).
- Response contains no private-key material (scanned for private/key_enc/fernet).
- DB: `wallet_address` plaintext, `wallet_key_enc` Fernet-encrypted (≠ plaintext);
  decrypt round-trip → `Account.from_key` recovers the same address.
- GET /a/{name} renders: wallet address, copy button, deal cards, verification
  count; unknown name → 404. Author names on cards/detail link to profiles.
- Backfill: legacy agent row (NULL wallet cols) gets a wallet from
  `ensure_wallets()` at boot; second run is a no-op. (Note: SQLite has no
  `ADD COLUMN IF NOT EXISTS`, so migration uses a PRAGMA table_info guard.)

**Withdrawals on Base (13 checks, all passed)**
- `withdrawal_value_wei` unit tests: "all" = balance − 21000·gas_price; explicit
  amounts pass through; 422 on balance<gas, amount>balance, zero, negative,
  garbage, missing amount.
- GET /v1/wallet/balance against Base mainnet RPC (read-only): returns
  `{"wallet_address","balance_eth"}`; fresh wallet → "0". No key → 401,
  bad key → 401.
- POST /v1/wallet/withdraw: bad destination → 422; own address → 422;
  unfunded wallet → 422 "insufficient balance to cover amount + gas" (no
  broadcast attempted); error bodies leak no key material.
- Auth scoping: agent B's decrypted key only ever signs as B's wallet
  (offline sign + `Account.recover_transaction`); A unreachable via B.
- Rate limit: 5 successful withdrawals then 6th → 429 (mocked-chain test;
  each of the 5 was verified signed by the agent's own wallet).

**Regression (28 checks, all passed)**
- All original behaviors re-verified: pages, register validation, deal CRUD,
  server-side scores, verify/flag rules, filters, XSS escaping, admin
  hide/unhide/delete + 401 without token, delete-own-only 403.

Nothing deployed; no Railway, no domains touched.

---

## Real-listing swap (2026-09-16 ~15:05 UTC)
- Deployment 09bdba48 (wallets) confirmed SUCCESS before starting.
- Deleted 6 seeded fake deals via admin API (ids 1,2,3,4,5,7 — the Aeron/Civic/boxes/Patagonia/KitchenAid/Sony placeholders by "fren"). Also deleted id 8, a real Aeron post that landed from a flaked-then-retried request, to keep attribution clean.
- Registered new agent "realscout" (wallet 0x2f53c0dfE1D055bAE2673cE26560E894eeE3e365). NOTE: registration responses intermittently hit RemoteDisconnected from the sandbox (server processes the request, response lost) — burned 3 stranded names (stealscout, stealscout_nyc, stealscout_ny, nycdealscout) before one clean response; deal posts used title-dedup after flakes, zero duplicates resulted.
- Scanned live FB Marketplace near Queens via facebook-cli (6 queries x 10 listings). Honesty rule: only listings whose DESCRIPTION states retail value were posted (4 found); 2 free-box listings added (score 99 by rule, no market price needed).
- Posted 6 real deals as realscout: ids 9-14.
  - Herman Miller Aeron 2015 size B (broken gas cylinder caveat in description), $250 vs $1,700 retail, Jersey City NJ, score 85
  - KitchenAid Pro 600 bundle (mixer + processor + juicer, speed-1 glitch caveat), $350 vs $1,200 retail, Hoboken NJ, score 71
  - KitchenAid Pro 600 Empire Red never-used, $325 vs ~$550 retail, Jersey City NJ, score 41
  - Patagonia Insulated Barn Coat M olive barely-worn, $125 vs $199 retail, Brooklyn NY, score 37
  - FREE wardrobe boxes (up to 8), Jersey City NJ, score 99
  - FREE moving boxes + bubble wrap, Somerville NJ, score 99
- Verified: GET /v1/deals = exactly 6, all by realscout, zero fakes; /d/9 detail page 200 with title/caveat/source URL/score; / and /a/realscout 200.

---

## Daily deal scanner (2026-09-16 ~18:45 UTC)
- Built `scanner.py`: registers agent "marketscout" once (key at ~/.stealfeed_scanner_key, mode 600; reused on later runs), scans 12 facebook-cli queries x12 listings, applies junk filters (no price / <3-word title / no image / spam markers / already-posted URL / unavailable status / missing condition / older than 14d), then posts only honestly-priced steals: (a) seller-stated retail via regex, or (b) curated KNOWN_RETAIL dict (aeron 1745, eames 6495, xm4 349, switch oled 349 / switch 299, airpods pro 249, kitchenaid artisan 499 / professional 649, patagonia down sweater 229, dyson v8 449) with price <= 60% of retail. Max 8/run, exact-title + source_url dedup, decisions logged to hidden_scans/scan-YYYY-MM-DD.log, --dry-run flag.
- Added curl fallback for the sandbox IncompleteRead flake on large API responses (per AGENTS.md pattern).
- Dry-run first: 8 qualified, 33 skipped (too old 15, no knowable value 8, short title 7, already posted 1, dup title 1).
- Live run: registered marketscout cleanly; posted 7 (ids 15-18, 20-22), 8th POST hit HTTP 502 during the wallet-removal deploy rollout — row had landed (id 23), confirming the flake pattern. Fixed scanner with verify-then-retry for POSTs (never blind-retry non-idempotent calls).
- One duplicate slipped through (ids 19+20, same title — curl fallback re-POSTed after a deploy-time connection drop); deleted id 20 via DELETE /v1/deals/20 with the agent key. Feed now 14 deals total, zero dupes.
- Final marketscout posts (all real FB listings with source URLs): Aeron Basic B $335 score 81; Aeron Chair $450 score 74; Aeron Remastered+footrest $500 score 71; Aeron Remastered $920 score 47; Aeron office chair B $300 score 83; AirPods Pro MagSafe $60 score 76; AirPods Pro 1st Gen $60 score 76; Airpods Pro Gen 2 $45 score 82.
- Caveat: $45-60 AirPods are plausibly counterfeit — scores are honest vs retail but buyers should treat with caution (flag system is the remedy).
- Second dry-run after fixes: previously posted 8 correctly deduped as "already posted"; new candidates surfaced (Aeron $420, more AirPods/Switches). Dedup verified end-to-end.
- Confirmed live: wallet-removal build deployed (/llms.txt has 0 wallet mentions).
- Cron NOT created: no cron tooling available in this subagent's namespace. Spec for parent: name `stealfeed-daily-scan`, daily 09:00 America/New_York, command `python3 ~/workspace/stealfeed/scanner.py`.

## 2026-09-16 — comparable-listings fallback (no-retail steals)
Gregory's point: listings can be genuine steals even with no stated retail —
via rarity or by comparing against other items. Implemented the honest
version: when a listing has no seller-stated or known retail value, the
scanner compares its price against the median asking price of comparable
live listings from the same search query (>=5 needed, price <= 60% of
median). The median is an observed market signal — nothing invented — and
posted deals say so explicitly ("typical asking $X across N similar
listings (asking prices, not confirmed sales)").
Tier hygiene (from dry-run review):
- replicas ("replica"/"faux"/"dupe"/..., plus "<brand> style" like "Eames
  Style") only comp against replicas; genuine against genuine.
- parts/damaged titles get no comps at all.
- known-retail rule now skips plural accessories ("AirPods Pro Cases" $2).
Rarity (true 1-of-1s with no comps) stays a human/agent judgment call —
post manually with a justified market value.
Unit tests: 8 tier-hygiene cases pass. Dry run: 8 clean picks, incl. a
$200 faux Eames correctly tiered vs 5 replica comps ($380 median).

## 2026-09-16 — agent scan requests ("find deals on honda")
New API: POST /v1/scan-requests {queries[1-10], max_results[1-20]} ->
pending request; worker (scanner.py --process-queue, every 15 min via
stealfeed-scan-queue cron) searches live Marketplace per query, evaluates
with the same junk filters + scoring, and POSTS each qualifier immediately
under the requesting agent's name (admin-only post_as_agent_id override),
until max_results. Poll GET /v1/scan-requests/{id} for status + embedded
deals. Limits: 3 queued/running per agent; stale "running" (>2h) re-queued.
Arbitrary queries get a category via infer_category() keyword map.
Documented in /join and /llms.txt (sections 6 and 7).
E2E verified live: agent "hondahunter" requested ["honda civic",
"honda accord"] max 6 -> worker found 13 accord listings, posted 1 deal
(id 24, 2007 Honda Accord EX Coupe $1,200 vs $2,500 typical, score 52,
category cars, author=hondahunter), request done.
Also fixed: "Seller notes issues:" caveat line was computed but never
appended to descriptions (caveat_bits dropped); now appended, with "issue"
flagged unless the seller says "no issues".

## 2026-09-16 — no-limit hunts + 100-mile radius + listings_seen
Per Gregory ("there should be no limit"): removed the 1-10 query cap, the
20-result cap, and the 3-active-scans-per-agent cap. max_results is now
optional and unbounded (omit = post every qualifier); the DB's NOT NULL
max_results column stores 0 as the unlimited sentinel, surfaced as null in
the API. Large hunts stream ~10 queries per worker pass: request returns to
"pending" between passes, queries_done + deal_ids + listings_seen accumulate
server-side, final pass marks done. /join + /llms.txt updated.
Geography + volume (Gregory: "100 mile radius", "as many pings as
possible"): search_marketplace now passes --latitude/--longitude
(NYC 40.7128,-74.0060 default) + --radius-in-miles 100 to facebook-cli and
follows paging cursors up to 5 pages x 20 = ~100 listings per query
(previously 12, no geo). POST /v1/scan-requests accepts optional
latitude/longitude/radius_in_miles (1-500). Responses carry listings_seen
(how many listings were looked through) and search_area.
Trade-only filter: WTT / "want to trade" / "trade only" etc. listings now
skip — their placeholder prices made steal math bogus (a WTT bundle had
scored 99). Cash-open listings ("or trade", "trade + cash") still qualify.
E2E verified live: agent "pingtester", 12 queries, no max_results ->
pass 1: 10 queries, 825 listings seen, 163 deals posted, back to pending
(queries_done 10/12); pass 2: 2 queries, 174 listings, 38 deals, done
(queries_done 12/12, listings_seen 999, 201 deals total, max_results null).
Daily scan also uses the 100-mile radius + pagination; its summary now
reports total listings looked through.

## 2026-09-16 — removed two trade-only test deals
Gregory approved removing deals 231 ("Trade only Nintendo switch 2 for a
PS5") and 233 ("WTT: Ultimate Console Bundle", score 99) — posted by the
pingtester E2E run before the trade-only filter shipped. Deleted via admin
API; verified absent from the public deals list.

## 2026-09-16 — duplicate cleanup (51 rows) + duplicate prevention shipped
Gregory reported repeated listings on the board. Root cause: the scheduled
queue worker and a manually started worker processed scan request 2
concurrently; per-process in-memory dedup couldn't stop the other process
from posting the same URLs. Deleted 41 exact-duplicate source_url rows (kept
earliest per URL), then 10 more exact duplicate pairs found in the newest
200, then 4 more low-ID pairs (ids 26/27, 30/31, 32/33, 34/35) found via the
new admin endpoint — 51 rows total, all pingtester test deals, plus deals
231/233 (trade-only, separately approved). Full-table audit now returns zero
duplicate groups. Remaining nonduplicate pingtester deals kept per Gregory
(no permission to remove).
Prevention (all live): atomic POST /v1/admin/scan-requests/{id}/claim
(pending->running, exactly one worker wins; losers get claimed:false and skip);
workers heartbeat/checkpoint after every query (2h stale reclaim);
fetch_posted_urls pages the whole board; Facebook listing-ID extraction
(/marketplace/item/{id}) added to scanner dedup; unique index
idx_deals_source_url live on deals(source_url); POST /v1/deals on a duplicate
link returns 409 {"code":"already_posted","id","steal_score"} (no new row),
treated by the scanner as already-posted, not a failure.

## 2026-09-16 — keyword search on site + API (Gregory: "allow users to search for keywords on the site too")
Site filter form now has a search box (?kw=, matches title+description);
API GET /v1/deals takes q= with the same matching. Docs (/join, /llms.txt)
updated. Verified live: 200 cards unfiltered, 18 for "bike", 0 for nonsense;
API q=bike -> 18 deals.
Self-inflicted bug during this work: named the new API param `q`, colliding
with the SQL-string local also named `q` in list_deals — every request
embedded the SQL text into the LIKE pattern and the board read empty for
~10 min. Fixed by renaming the SQL local to `sql`. Lesson: never name a
query param the same as a reused local.

## 2026-09-16 — verification of dedup fixes (live, deployment 4239c1d8)
- claim race: created scan request 3 (agent verifybot), two claim calls ->
  first {"claimed":true}, second {"claimed":false}; exactly one winner.
- duplicate POST: same source_url twice -> 200 (id 282), then 409
  {"code":"already_posted","id":282}; test row deleted after.
- pagination: /v1/deals?limit=200&offset=0 -> 200 rows, offset=200 -> 15 rows.
- site health: 200, search box renders and filters.
Notes: throwaway agent "verifybot" (id 13) left on the network, harmless.
Scan request 3 left "running"; goes stale in 2h and its nonsense query
("test query xyz", max_results 1) is expected to post nothing.

## 2026-09-16 — Honda hunt for Gregory + parts-only filter
Gregory: his own Facebook searches for "honda"/"honda civic" return tons of
listings; the hunter must match that and post a lot of them. Verified the
search path healthy (20/page live for "honda civic" — the old 0-result run
predated pagination). Created scan request 4 (queries: honda civic, honda
accord, honda cr-v) under @fren and ran the queue worker: 259 listings seen,
24 deals posted, request done. Server-side 409 dedup caught a repost live
("already posted (server dedup, id=314)").
Quality catch: deal 306 "2023 Honda CR-V" at $0 score 99 was a "Part olny"
(parts-only) listing — deleted. Added parts-only markers ("part only",
"parts only", "for parts", "parts car") to the scanner junk filters next to
the trade-only ones.
Gregory's standing rule: when he says "look X up", report results AND post a
lot of them as steals (saved to MEMORY.md).

## 2026-09-16 — "hunt this for me" button (visitor hunt requests)
Gregory approved: empty keyword search now shows a "hunt this for me" button
instead of just the dead-end message. Clicking POSTs to /hunt, which queues a
scan request under a get-or-created system agent `sitehunts` (key never handed
out — visitor hunts don't need agent keys and don't impersonate anyone).
- Rate limit: 5 hunts/hour per IP (new hunt_rl table, IP hashed).
- Dedup: same query already pending/running -> no duplicate request.
- Empty states: fresh button / "hunt queued" (just clicked) / "already running"
  / "too many hunts" — honest, no results promised.
- Hunt results post under @sitehunts; scan-request detail stays scoped to the
  owning agent ("not your scan request" for others).
Verified live: button renders on empty kw search; POST -> 303 ?hunted=1;
re-POST same query -> ?hunted=already; rate-limit logic unit-tested (5 pass,
6th blocked).
Two deploy incidents, both fixed: (1) FastAPI Form needs python-multipart —
added to requirements.txt (first deploy crashed at boot); (2) index_html closed
the sqlite connection before the new empty-state branches ran
active_hunt_for(con) -> 500 on empty searches; fixed by computing the flag
before con.close().

## 2026-09-16 — site 200-item cap removed (pagination)
- Gregory noticed the board header read "200 steals on the board" and asked if only 200 show. Correct: `index_html` had a hardcoded `LIMIT 200` with no paging; the API already supported `limit`+`offset`.
- Fix (deployed as 184ac239): `?page=N` param (200/page), true total via `COUNT(*)` in the header ("N steals on the board"), and a "show more steals" link preserving filters when more pages exist. Verified render locally (p1/p2, empty search, sort=score); live verification pending deploy SUCCESS.

## 2026-09-16 — stuck scan-request claim incident (request 7, honda fit)
- Request 7 sat "running" 20+ min with 0 listings_seen. Root cause: sandbox transport flake on the non-idempotent claim POST — server recorded the claim, the response was lost, the api() curl-retry got `claimed:false`, and the worker skipped a request it actually owned. Same flake class as the registration stranding noted in AGENTS.md.
- Recovery: reset to pending via admin status, re-claimed back-to-back in one process, ran the hunt inline holding the claim. Lesson: for a stuck "running" request with a fresh started_at and zero progress, the claimer is usually a dead worker; reset+reclaim in a single script. Do NOT trust "already claimed by another worker" at face value after a flake — verify progress.
- Also fixed: `page` param name shadowed the `page()` template helper (TypeError) — renamed to `pageno`.

## 2026-09-16 — Honda Fit hunt (request 7, @fren)
- 3 queries (honda fit / sport / ex), 259 listings, 13 deals posted. Best: 2007 Fit Sport $800 New London CT (score 73); 2009 Fit Sport $1,400 Waterbury CT (53); 2012 Fit $1,800 New Britain CT (50); 2008 Fit Sport $2,000 Wyandanch NY (44); 2007 Fit $2,000 Bronx NY (44).

## 2026-09-16 — MCM house-redo hunt (request 8, @fren)
- 10 queries (daybed/couch/sofa/tv stand/credenza/sideboard/dresser/lounge chair/floor lamp), 692 listings, 94 deals posted. Best for Gregory: walnut MCM TV stand/media console $50 Park Slope (score 83, id 332); dark gray MCM sofa $70 Bay Shore (77, id 381); hard-rock maple MCM dresser $18 Pompton Plains NJ (96, id 400); Article linen daybed $550 Williamsburg (54, id 317); free MCM-style 3-cushion sofa Edison NJ (99, id 335); estate sale Sat 9/19 NYC (99, id 404).

## 2026-09-16 — pagination deploy 500 (fixed)
- Deploy 184ac239 (pagination) returned SUCCESS but `/` and `/?page=2` 500'd live. Root cause: the `page`→`pageno` rename missed one f-string (`{page * PAGE_SIZE}` — quote style differed so the replace didn't match), leaving a reference to the `page()` template helper. Local tests passed because the test DB had 0 deals and the show-more branch never executed.
- Lesson: always exercise new branches with data that triggers them (rebuilt a 250-deal local DB to verify).
- Fix redeployed as 99c1979f; verified locally with 250 deals (true total in header, show-more on p1, none on p2, hunt button intact).
