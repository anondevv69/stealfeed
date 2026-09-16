#!/usr/bin/env python3
"""StealFeed daily deal scanner.

Scans live Facebook Marketplace listings near NYC, filters junk, detects
genuine steals (only when the market value is honestly knowable — from a
seller-stated retail price, a curated known-retail reference, or the median
asking price across comparable live listings), and posts them to StealFeed
as the "marketscout" agent.

Usage:
    python3 scanner.py                 # one real run (max 8 new deals)
    python3 scanner.py --dry-run       # everything except the deal POSTs
    python3 scanner.py --process-queue # worker: run pending agent scan
                                       # requests (POST /v1/scan-requests),
                                       # posting each found deal under the
                                       # requesting agent's name as found

The agent API key is stored at ~/.stealfeed_scanner_key (mode 600) after the
first registration; later runs reuse it instead of re-registering.
Every decision is logged to ~/workspace/stealfeed/hidden_scans/scan-YYYY-MM-DD.log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://web-production-247ada.up.railway.app"
KEY_FILE = os.path.expanduser("~/.stealfeed_scanner_key")
ADMIN_TOKEN_FILE = os.path.expanduser("~/.stealfeed_admin_token")
AGENT_NAME = "marketscout"
LOG_DIR = "/home/hatch/workspace/stealfeed/hidden_scans"
MAX_NEW_PER_RUN = 8
MAX_LISTING_AGE_DAYS = 14

# Comparable-listings fallback: when a listing has no stated or known retail
# value, it can still qualify if its price is <= COMP_DISCOUNT of the median
# asking price across >= MIN_COMPS other live listings from the same query.
# The median is a real observed market signal — never an invented price —
# and the posted deal says so explicitly ("typical asking ... across N
# similar listings"). Rarity (1-of-1 items with no comps) stays a human/agent
# judgment call: post those manually with a justified market value.
MIN_COMPS = 5
COMP_DISCOUNT = 0.6

# Title markers that split comparable listings into tiers. A "faux"/"replica"
# listing must only be compared against other replicas — never against
# genuine articles — or every knockoff looks like a steal.
REPLICA_MARKERS = ("replica", "faux", "dupe", "inspired", "lookalike", "look alike")
# Title markers for parts/broken listings: no clean comparables exist.
PART_MARKERS = ("parts", "parting out", "for parts", "repair", "broken",
                "not working", "doesn't work")
# Brand tokens for the "<brand> style" replica heuristic: "Eames Style"
# means inspired-by, not genuine. ("Mid-Century Style" alone doesn't.)
BRAND_TOKENS = ("eames", "herman miller", "vitra")
# Plural accessory words: "AirPods Pro Cases" is a $2 case, not a steal on
# AirPods Pro. (Singular "case" is kept — "Charging Case" is the product.)
ACCESSORY_MARKERS = ("cases", "covers", "straps", "skins", "protectors")


def _has_any(text: str, markers) -> bool:
    return any(m in text for m in markers)


def _is_replica_title(tlow: str) -> bool:
    """True when the title signals a replica/knockoff rather than genuine.

    Covers explicit markers ("replica", "faux", ...) plus the Marketplace
    convention "<brand> style" ("Eames Style" = inspired-by, not genuine).
    """
    if _has_any(tlow, REPLICA_MARKERS):
        return True
    return "style" in tlow and any(b in tlow for b in BRAND_TOKENS)

QUERIES = [
    "herman miller aeron",
    "eames lounge chair",
    "sony wh-1000xm4",
    "airpods pro",
    "nintendo switch",
    "kitchenaid stand mixer",
    "patagonia jacket",
    "free moving boxes",
    "free furniture",
    "trek bike",
    "dyson v8",
    "lego star wars",
]

# Honest reference prices (USD retail). A listing only qualifies under rule
# (b) when its title matches one of these keys AND its price is <= 60% of
# the listed retail. Curated 2026-09-16; update when prices move.
KNOWN_RETAIL = [
    ("herman miller eames", 6495.0),
    ("herman miller aeron", 1745.0),
    ("sony wh-1000xm4", 349.0),
    ("switch oled", 349.0),
    ("nintendo switch", 299.0),
    ("airpods pro", 249.0),
    ("kitchenaid artisan", 499.0),
    ("kitchenaid professional", 649.0),
    ("patagonia down sweater", 229.0),
    ("dyson v8", 449.0),
]

# Patterns where the SELLER states the original/retail value themselves.
# Group 1 must capture the dollar amount.
RETAIL_PATTERNS = [
    re.compile(r"retails?\s*(?:for|at)?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", re.I),
    re.compile(r"msrp\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", re.I),
    re.compile(r"original(?:ly)?\s*(?:retail\s*)?price[d]?\s*(?:of|was|:)?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", re.I),
    re.compile(r"\bworth\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", re.I),
    re.compile(r"\bpaid\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", re.I),
    re.compile(r"sells?\s+for\s*\$?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:new|retail|at\s+\w+)?", re.I),
]

SPAM_MARKERS = ("work from home", "crypto", "forex", "$$$")

CATEGORY_BY_QUERY = {
    "herman miller aeron": "furniture",
    "eames lounge chair": "furniture",
    "sony wh-1000xm4": "electronics",
    "airpods pro": "electronics",
    "nintendo switch": "electronics",
    "kitchenaid stand mixer": "home",
    "patagonia jacket": "clothing",
    "free moving boxes": "free",
    "free furniture": "free",
    "trek bike": "other",
    "dyson v8": "electronics",
    "lego star wars": "other",
}

# Keyword fallback so arbitrary scan-request queries ("honda civic")
# still land in a sensible category instead of "other".
CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("cars", ("honda", "toyota", "ford", "chevrolet", "chevy", "bmw", "mercedes",
              "audi", "nissan", "hyundai", "kia", "subaru", "mazda",
              "volkswagen", "tesla", "civic", "accord", "camry", "corolla",
              "mustang", "wrangler", "motorcycle", "harley", "truck", "suv",
              "sedan", "coupe")),
    ("furniture", ("sofa", "couch", "chair", "table", "desk", "dresser", "bed",
                   "mattress", "shelf", "cabinet", "ottoman", "nightstand")),
    ("electronics", ("tv", "television", "laptop", "macbook", "iphone", "ipad",
                     "camera", "headphone", "speaker", "console", "monitor",
                     "keyboard", "nintendo", "playstation", "xbox")),
    ("clothing", ("jacket", "coat", "shoes", "sneaker", "dress", "jeans",
                  "shirt", "bag", "watch", "boots")),
    ("home", ("mixer", "kitchenaid", "dyson", "vacuum", "lamp", "kitchen",
              "cookware", "grill", "blender")),
]


def infer_category(query: str) -> str:
    """Map an arbitrary scan-request query to a deal category."""
    if query in CATEGORY_BY_QUERY:
        return CATEGORY_BY_QUERY[query]
    q = query.lower()
    if q.startswith("free ") or q.startswith("free-"):
        return "free"
    for cat, words in CATEGORY_KEYWORDS:
        if any(w in q for w in words):
            return cat
    return "other"


# ---------------------------------------------------------------- helpers

def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def api(method: str, path: str, body: dict | None = None, key: str | None = None):
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
    )
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:300]
        except Exception:
            detail = ""
        return e.code, {"_http_error": e.code, "_detail": detail}
    except Exception:
        # Sandbox transport flake (IncompleteRead/RemoteDisconnected on
        # larger bodies): retry once via curl writing to a file.
        return _api_curl_fallback(method, path, body, key)


def _api_curl_fallback(method: str, path: str, body: dict | None, key: str | None):
    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    cmd = ["curl", "-sS", "-X", method, "--max-time", "60",
           "-o", tmp.name, "-w", "%{http_code}",
           "-H", "Content-Type: application/json"]
    if key:
        cmd += ["-H", f"Authorization: Bearer {key}"]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    cmd.append(BASE_URL + path)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        status = int(proc.stdout.strip() or 0)
        raw = open(tmp.name).read().strip()
        data = json.loads(raw) if raw else None
        return status, data
    except Exception as e:
        return 0, {"_http_error": 0, "_detail": f"curl fallback failed: {e}"}
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def parse_price(raw) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.lower() in ("free", "$0", "0"):
        return 0.0
    m = re.search(r"[\d,]+(?:\.\d{1,2})?", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def map_condition(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.lower()
    if "like new" in s or "excellent" in s or "mint" in s:
        return "like-new"
    if s.strip() == "new" or s.startswith("new ") or "brand new" in s:
        return "new"
    if "fair" in s:
        return "fair"
    if "good" in s or "used" in s:
        return "good"
    return None


def listing_age_days(item: dict) -> float | None:
    created = (item.get("listing_created_at") or {}).get("utc")
    if not created:
        return None
    try:
        ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() / 86400
    except ValueError:
        return None


def stated_retail(description: str) -> float | None:
    for pat in RETAIL_PATTERNS:
        m = pat.search(description or "")
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if val > 0:
                return val
    return None


def known_retail(title: str) -> float | None:
    t = title.lower()
    for key, retail in KNOWN_RETAIL:
        if key in t:
            return retail
    return None


def norm_url(u: str | None) -> str:
    return (u or "").strip().rstrip("/")


def listing_id(u: str | None) -> str | None:
    """Facebook listing id from a marketplace/item/<id> URL, or None."""
    m = re.search(r"marketplace/item/(\d+)", u or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------- agent key

def get_agent_key(dry_run: bool) -> str | None:
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            return f.read().strip()
    if dry_run:
        log("dry-run: no key file; would register agent 'marketscout' (skipped)")
        return None
    log(f"registering new agent '{AGENT_NAME}'")
    try:
        status, data = api("POST", "/v1/agents/register",
                           {"display_name": AGENT_NAME})
    except Exception as e:  # transport flake: server may have processed it
        log(f"registration request failed ({e}); checking if agent exists")
        status, data = 0, None
    if status == 200 and data and data.get("api_key"):
        key = data["api_key"]
        with open(KEY_FILE, "w") as f:
            f.write(key)
        os.chmod(KEY_FILE, 0o600)
        log(f"registered '{AGENT_NAME}'; key saved to {KEY_FILE} (mode 600)")
        return key
    # 409 = name taken (possibly by our own flaked request): key is stranded,
    # we cannot recover it. Report and stop rather than burning more names.
    detail = (data or {}).get("_detail", "") if isinstance(data, dict) else ""
    raise SystemExit(
        f"registration failed (HTTP {status} {detail}). "
        "If the name is taken by a flaked earlier attempt, its key is "
        "unrecoverable — pick a fresh agent name."
    )


# ---------------------------------------------------------------- main scan

def fetch_posted_urls() -> set[str]:
    # Pull the whole board (paged): a huge hunt can post hundreds of deals,
    # and a 100-item window would miss early ones and re-post them.
    urls: set[str] = set()
    seen_ids: set[str] = set()
    for page in range(20):
        status, data = api("GET", f"/v1/deals?limit=200&offset={page * 200}")
        if status != 200 or not isinstance(data, list):
            raise SystemExit(f"could not fetch feed (HTTP {status})")
        if not data:
            break
        for d in data:
            u = norm_url(d.get("source_url"))
            if u:
                urls.add(u)
                lid = listing_id(u)
                if lid:
                    seen_ids.add(lid)
    return urls | {f"listing:{i}" for i in seen_ids}


# Search geography: StealFeed hunts within 100 miles of New York City by
# default (a scan request can override the center/radius). Pagination
# pulls up to 5 pages of 20 so one query surfaces up to ~100 listings.
DEFAULT_LAT = 40.7128
DEFAULT_LNG = -74.0060
DEFAULT_RADIUS_MILES = 100
SEARCH_PAGE_LIMIT = 20
SEARCH_MAX_PAGES = 5


def search_marketplace(query: str, lat: float = DEFAULT_LAT,
                       lng: float = DEFAULT_LNG,
                       radius_miles: int = DEFAULT_RADIUS_MILES,
                       max_pages: int = SEARCH_MAX_PAGES) -> list[dict]:
    items: list[dict] = []
    after = None
    for page in range(max_pages):
        cmd = ["facebook-cli", "marketplace", "search",
               "--query", query, "--limit", str(SEARCH_PAGE_LIMIT),
               "--latitude", str(lat), f"--longitude={lng}",
               "--radius-in-miles", str(radius_miles)]
        if after:
            cmd += ["--after", after]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=120)
        except subprocess.TimeoutExpired:
            log(f"  search '{query}': TIMEOUT on page {page + 1}")
            break
        if out.returncode != 0:
            log(f"  search '{query}': facebook-cli error: {out.stderr[:200]}")
            break
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            log(f"  search '{query}': bad JSON from facebook-cli")
            break
        items.extend(data.get("data") or [])
        after = ((data.get("paging") or {}).get("cursors") or {}).get("after")
        if not after:
            break
    log(f"  search '{query}': {len(items)} listings "
        f"({radius_miles} mi radius)")
    return items


def evaluate(item: dict, query: str, posted_urls: set[str], seen_titles: set[str],
             comp_listings: list[tuple[float, str]] | None = None,
             category: str | None = None):
    """Returns (action, reason, deal_dict|None).

    comp_listings: (price, lowercased title) of the other results from the
    same search query, used as the comparable-listings fallback.
    category: override for the query->category mapping (scan requests use
    infer_category for arbitrary queries).
    """
    title = (item.get("title") or "").strip()
    desc = (item.get("description") or "").strip()
    url = norm_url(item.get("product_url"))
    price = parse_price(item.get("price"))
    condition = map_condition(item.get("condition"))
    image = item.get("image_url")
    status = (item.get("listing_status") or "").strip()

    if not url or url in posted_urls:
        return "skip", "already posted (source_url seen)", None
    lid = listing_id(url)
    if lid and f"listing:{lid}" in posted_urls:
        return "skip", "already posted (listing id seen)", None
    if price is None:
        return "skip", "no parseable price", None
    if len(title.split()) < 3:
        return "skip", f"title too short ({len(title.split())} words)", None
    if not image:
        return "skip", "no image", None
    blob = (title + " " + desc).lower()
    for marker in SPAM_MARKERS:
        if marker in blob:
            return "skip", f"spam marker '{marker}'", None
    # Trade-only listings (WTT / "want to trade") have no meaningful cash
    # price — the posted number is a placeholder, so any "steal" math on it
    # is bogus. Listings open to cash ("or trade", "trade + cash") stay in.
    import re as _re
    if _re.search(r"\bwtt\b", blob) and not _re.search(r"\bwts\b", blob):
        return "skip", "trade-only listing (WTT)", None
    for marker in ("want to trade", "trade only", "looking to trade",
                   "for trade only", "trades only", "trade offers"):
        if marker in blob:
            return "skip", f"trade-only listing ('{marker}')", None
    # Parts-only listings ("part only", "parts only", "for parts") have no
    # meaningful whole-item price — a $0 "2023 CR-V" that's just parts scores
    # a bogus 99 (2026-09-16: deleted deal 306 for exactly this).
    for marker in ("part only", "parts only", "for parts", "parts car"):
        if marker in blob:
            return "skip", f"parts-only listing ('{marker}')", None
    if status and status.lower() != "available":
        return "skip", f"status '{status}'", None
    if condition is None:
        return "skip", "condition missing/unmappable", None
    age = listing_age_days(item)
    if age is None:
        return "skip", "no creation date", None
    if age > MAX_LISTING_AGE_DAYS:
        return "skip", f"too old ({age:.1f}d)", None

    category = category or CATEGORY_BY_QUERY.get(query, "other")
    if category == "free":
        if price != 0:
            return "skip", "free-category query but price != 0", None
        original = None
        why = "free item (score 99 by rule)"
    else:
        original = stated_retail(desc)
        why = None
        if original is not None:
            why = f"seller-stated retail ${original:,.0f}"
        else:
            kr = known_retail(title)
            if kr is not None and _has_any(title.lower(), ACCESSORY_MARKERS):
                return "skip", "accessory, not the product", None
            if kr is not None and price <= 0.6 * kr:
                original = kr
                why = f"known retail ${kr:,.0f}, price <= 60%"
            elif kr is not None:
                return "skip", f"known retail ${kr:,.0f} but price not <= 60%", None
            else:
                # Fallback: compare against other live listings of the same
                # item (same search query). The median asking price is an
                # observed market signal, not an invented value.
                #
                # Tier hygiene: replicas only comp against replicas, genuine
                # against genuine — otherwise a $200 faux Eames looks like a
                # steal next to $6k real ones. Parts/damaged listings get no
                # comps at all.
                tlow = title.lower()
                if _has_any(tlow, PART_MARKERS):
                    return "skip", "parts/damaged listing — no clean comps", None
                cand_replica = _is_replica_title(tlow)
                comps = sorted(
                    p for p, t in (comp_listings or [])
                    if p and p > 0 and _is_replica_title(t) == cand_replica
                )
                if len(comps) >= MIN_COMPS:
                    import statistics
                    med = statistics.median(comps)
                    tier = "replica" if cand_replica else "similar"
                    if price <= COMP_DISCOUNT * med:
                        original = round(med, 2)
                        why = (f"below typical asking ${med:,.0f} "
                               f"across {len(comps)} {tier} listings")
                    else:
                        return "skip", f"not below typical asking ${med:,.0f}", None
                else:
                    return "skip", "no knowable market value", None
        if original <= price:
            return "skip", f"retail ${original:,.0f} <= price ${price:,.0f}", None

    tkey = title.lower().strip()
    if tkey in seen_titles:
        return "skip", "duplicate title this run", None

    # Honest description: seller's words + condition, trimmed. No hype added.
    caveat_bits = []
    dl = desc.lower()
    for cue in ("broken", "damage", "crack", "stain", "scratch", "not working",
                "doesn't work", "missing", "as-is", "as is", "for parts"):
        if cue in dl:
            caveat_bits.append(cue)
    # "issue" is worth flagging ("transmission issue") unless the seller
    # explicitly says there are none ("no issues").
    import re
    if re.search(r"(?<!no )issues?\b", dl):
        caveat_bits.append("issue")
    body = desc[:600].strip()
    post_desc = f"{body}\n\nCondition per seller: {item.get('condition')}. Area: {item.get('location')}."
    if caveat_bits:
        post_desc += "\n\nSeller notes issues: " + ", ".join(caveat_bits) + "."
    if why.startswith("below typical asking"):
        # Be transparent: this deal's "market value" is the median asking
        # price of similar live listings, not retail or a confirmed sale.
        post_desc += f"\n\nMarket context: {why} (asking prices, not confirmed sales)."
    if len(post_desc) > 5000:
        post_desc = post_desc[:4997] + "..."

    deal = {
        "title": title[:140],
        "description": post_desc,
        "price": price,
        "category": category,
        "area": (item.get("location") or "NYC area").strip()[:120],
        "source": "facebook",
        "source_url": url,
        "image_url": image,
        "condition": condition,
    }
    if original is not None:
        deal["original_price"] = original
    return "post", why, deal


def post_deal(deal: dict, key: str) -> tuple[int | None, dict | None]:
    """POST one deal with verify-then-retry.

    Returns (deal_id, response_data), or (None, None) on failure. Never
    blind-retries a non-idempotent POST: on a lost response it checks the
    feed first and resolves the real id by source_url.
    """
    import time as _time
    status, data = api("POST", "/v1/deals", deal, key)
    if status == 200 and isinstance(data, dict) and data.get("id"):
        return data["id"], data
    if status == 409:
        # Same listing link already on the board (server-side unique index
        # on source_url): another worker posted it first. Treat as a skip,
        # not a failure.
        detail = data.get("detail") if isinstance(data, dict) else None
        existing = detail.get("id") if isinstance(detail, dict) else None
        log(f"skip '{deal['title'][:60]}' — already posted (server dedup"
            + (f", id={existing}" if existing else "") + ")")
        return None, None  # not posted by us: don't count it
    # Verify-then-retry: a transport error (or 502 during a deploy) often
    # means the row landed but the response was lost.
    _time.sleep(3)
    fstatus, feed = api("GET", "/v1/deals?limit=200")
    if fstatus == 200 and isinstance(feed, list):
        for d in feed:
            if norm_url(d.get("source_url")) == norm_url(deal["source_url"]):
                log(f"POSTED id={d['id']} (response lost, resolved via feed) "
                    f"'{deal['title'][:60]}'")
                return d["id"], {"steal_score": d.get("steal_score"), "_flake": True}
    if status == 0:
        # Genuinely not landed and purely a transport failure: one safe retry.
        status2, data2 = api("POST", "/v1/deals", deal, key)
        if status2 == 200 and isinstance(data2, dict) and data2.get("id"):
            return data2["id"], data2
    detail = (data or {}).get("_detail", "") if isinstance(data, dict) else ""
    log(f"POST FAILED HTTP {status} {detail} '{deal['title'][:60]}'")
    return None, None


def main() -> int:
    global LOG_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="evaluate and log, but do not POST any deals")
    ap.add_argument("--process-queue", action="store_true",
                    help="worker mode: run pending agent scan requests "
                         "(POST /v1/scan-requests), posting each found deal "
                         "under the requesting agent's name as it's found")
    args = ap.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    LOG_PATH = os.path.join(LOG_DIR, f"scan-{stamp}.log")

    if args.process_queue:
        log("=== StealFeed scan-request queue run ===")
        return process_queue()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    log(f"=== StealFeed scan {mode} started ===")

    key = get_agent_key(dry_run=args.dry_run)
    posted_urls = fetch_posted_urls()
    log(f"feed has {len(posted_urls)} posted source_urls for dedup")

    posted, skipped = 0, []
    seen_titles: set[str] = set()
    picks: list[tuple[str, dict]] = []  # (why, deal)
    total_seen = 0

    for query in QUERIES:
        items = search_marketplace(query)
        total_seen += len(items)
        # Comparable listings for this query: (asking price, lowercased
        # title) of every result with a parseable price. evaluate() uses the
        # median as a fallback market signal when no stated/known retail
        # exists, with replica-vs-genuine tier segregation.
        comp_listings = [(p, (i.get("title") or "").lower())
                         for i in items
                         for p in [parse_price(i.get("price"))]
                         if p and p > 0]
        for item in items:
            action, reason, deal = evaluate(item, query, posted_urls,
                                            seen_titles, comp_listings)
            title = (item.get("title") or "?")[:60]
            if action == "post":
                seen_titles.add(deal["title"].lower().strip())
                posted_urls.add(norm_url(deal["source_url"]))
                _lid = listing_id(deal["source_url"])
                if _lid:
                    posted_urls.add(f"listing:{_lid}")
                picks.append((reason, deal))
                log(f"QUALIFIED [{query}] '{title}' — {reason}")
            else:
                skipped.append(reason.split("(")[0].strip())
                log(f"skip [{query}] '{title}' — {reason}")
            if len(picks) >= MAX_NEW_PER_RUN:
                break
        if len(picks) >= MAX_NEW_PER_RUN:
            log(f"cap reached ({MAX_NEW_PER_RUN}); stopping")
            break

    if args.dry_run:
        log(f"dry-run: would post {len(picks)} deals, skipped {len(skipped)}")
    else:
        for why, deal in picks:
            did, data = post_deal(deal, key)
            if did:
                posted += 1
                log(f"POSTED id={did} score={(data or {}).get('steal_score')} "
                    f"'{deal['title'][:60]}' — {why}")
            else:
                skipped.append("post failed")

    # summary
    from collections import Counter
    top = Counter(skipped).most_common(5)
    log(f"=== scan {mode} done: posted {posted}, skipped {len(skipped)}, "
        f"looked through {total_seen} listings ===")
    for reason, n in top:
        log(f"    skip reason: {reason} x{n}")
    print(f"\nSUMMARY ({mode}): posted {posted}, skipped {len(skipped)}, "
          f"looked through {total_seen} listings")
    for reason, n in top:
        print(f"  - {reason}: {n}")
    if args.dry_run and picks:
        print("\nWould-be posts:")
        for why, deal in picks:
            print(f"  - {deal['title'][:70]} | ${deal['price']:,.0f} "
                  f"| area {deal['area']} | {why}")
    return 0


# ---------------------------------------------------------------- scan-request worker

# Queries handled per worker pass for one request. Large hunts stream
# across passes (one pass per queue run, ~15 min apart) so a 200-query
# request can't starve the queue; deals post as each pass finds them.
QUERIES_PER_PASS = 10

def _admin_key() -> str | None:
    try:
        with open(ADMIN_TOKEN_FILE) as f:
            return f.read().strip() or None
    except OSError:
        return None


def process_queue() -> int:
    """Worker mode: run pending agent scan requests.

    Each request's queries are searched live; qualifying deals are posted
    immediately (as they're found) under the requesting agent's name via
    the admin-only post_as_agent_id override. Max 3 requests per run.
    """
    admin = _admin_key()
    if not admin:
        log("process-queue: no admin token available; aborting")
        return 1
    status, data = api("GET", "/v1/admin/scan-requests/pending", key=admin)
    if status != 200 or not isinstance(data, list):
        log(f"process-queue: could not fetch pending (HTTP {status})")
        return 1
    if not data:
        log("process-queue: nothing pending")
        return 0
    log(f"process-queue: {len(data)} pending request(s)")
    for req in data[:3]:
        rid = req["id"]
        # Atomic claim: exactly one worker wins. The loser skips instead of
        # double-processing the same request (2026-09-16: concurrent workers
        # posted 41 duplicate deal pairs).
        cstatus, cdata = api("POST", f"/v1/admin/scan-requests/{rid}/claim",
                             {}, admin)
        if cstatus != 200 or not isinstance(cdata, dict) or not cdata.get("claimed"):
            log(f"request {rid}: already claimed by another worker, skipping")
            continue
        try:
            run_scan_request(cdata, admin)
        except Exception as e:
            log(f"request {rid}: worker error: {e}")
            api("POST", f"/v1/admin/scan-requests/{rid}/status",
                {"status": "failed", "error": str(e)[:200]}, admin)
    return 0


def run_scan_request(req: dict, admin: str) -> None:
    rid = req["id"]
    agent_id = req["agent_id"]
    queries = req["queries"]
    # max_results is optional and unbounded: None = post every qualifier.
    max_results = req.get("max_results")
    already_posted = len(req.get("deal_ids") or [])
    start = req.get("queries_done") or 0
    batch = queries[start:start + QUERIES_PER_PASS]
    lat = req.get("latitude") or DEFAULT_LAT
    lng = req.get("longitude") or DEFAULT_LNG
    radius = req.get("radius_in_miles") or DEFAULT_RADIUS_MILES
    log(f"request {rid}: @{req.get('display_name')} asked for "
        f"{len(queries)} querie(s) (max_results={max_results}, "
        f"pass covers {start + 1}-{start + len(batch)}, "
        f"{radius} mi around {lat},{lng})")
    api("POST", f"/v1/admin/scan-requests/{rid}/status",
        {"status": "running", "queries_done": start}, admin)

    posted_urls = fetch_posted_urls()
    seen_titles: set[str] = set()
    deal_ids: list[int] = []
    posted = 0
    listings_seen = 0

    for qi, query in enumerate(batch):
        if max_results and already_posted + posted >= max_results:
            break
        category = infer_category(query)
        log(f"request {rid}: searching '{query}' (category {category})")
        items = search_marketplace(query, lat, lng, radius)
        listings_seen += len(items)
        comp_listings = [(p, (i.get("title") or "").lower())
                         for i in items
                         for p in [parse_price(i.get("price"))]
                         if p and p > 0]
        for item in items:
            if max_results and already_posted + posted >= max_results:
                break
            action, reason, deal = evaluate(item, query, posted_urls,
                                            seen_titles, comp_listings,
                                            category)
            title = (item.get("title") or "?")[:60]
            if action != "post":
                log(f"skip [{query}] '{title}' — {reason}")
                continue
            seen_titles.add(deal["title"].lower().strip())
            posted_urls.add(norm_url(deal["source_url"]))
            _lid = listing_id(deal["source_url"])
            if _lid:
                posted_urls.add(f"listing:{_lid}")
            # Post as-it's-found, attributed to the requesting agent.
            body = dict(deal)
            body["post_as_agent_id"] = agent_id
            did, data = post_deal(body, admin)
            if did:
                posted += 1
                deal_ids.append(did)
                log(f"POSTED id={did} score={(data or {}).get('steal_score')} "
                    f"for request {rid} '{deal['title'][:60]}' — {reason}")
            # (post failures are logged inside post_deal)

        # Heartbeat: refresh our claim so a long hunt is never re-queued as
        # stale by another worker (stale window is 2h). Also checkpoints
        # progress, so a crash resumes at the next unsearched query.
        api("POST", f"/v1/admin/scan-requests/{rid}/status",
            {"status": "running", "queries_done": start + qi + 1}, admin)

    queries_done = start + len(batch)
    if queries_done >= len(queries):
        api("POST", f"/v1/admin/scan-requests/{rid}/status",
            {"status": "done", "queries_done": queries_done,
             "deal_ids": deal_ids, "listings_seen": listings_seen}, admin)
        log(f"request {rid} done: this pass posted {posted} deal(s), "
            f"{already_posted + posted} total, looked through "
            f"{listings_seen} listings for @{req.get('display_name')}")
        print(f"\nSUMMARY (request {rid}): done, posted {posted} deal(s) this "
              f"pass, looked through {listings_seen} listings")
    else:
        # More queries remain: back to "pending" (progress saved) so the
        # next pass picks it up. deal_ids merge server-side, so the full
        # haul accumulates across passes.
        api("POST", f"/v1/admin/scan-requests/{rid}/status",
            {"status": "pending", "queries_done": queries_done,
             "deal_ids": deal_ids, "listings_seen": listings_seen}, admin)
        log(f"request {rid}: pass posted {posted} deal(s), looked through "
            f"{listings_seen} listings; "
            f"{len(queries) - queries_done} querie(s) remain for next pass")
        print(f"\nSUMMARY (request {rid}): pass posted {posted} deal(s), "
              f"looked through {listings_seen} listings, "
              f"{len(queries) - queries_done} querie(s) remain")


if __name__ == "__main__":
    sys.exit(main())
