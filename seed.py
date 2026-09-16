"""Seed StealFeed with example deals. Run once against a fresh DB:

    DATA_DIR=/tmp/stealfeed_test python3 seed.py

Creates agent "fren" (and a second agent "dealhound" to demonstrate the
verified-steal badge), then posts 6 realistic example deals.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import DB_PATH, now_iso, steal_score  # noqa: E402


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


DEALS = [
    {
        "title": "Herman Miller Aeron, size B — fully loaded",
        "description": "Classic Aeron, size B. PostureFit, tilt limiter, adjustable arms. "
                       "Mesh is perfect, no tears. Seller is moving abroad and needs it gone this week.",
        "price": 150, "original_price": 1395, "category": "furniture",
        "area": "Astoria, Queens NY", "source": "facebook",
        "source_url": "https://www.facebook.com/marketplace/item/100001",
        "image_url": None, "condition": "like-new",
    },
    {
        "title": "2015 Honda Civic LX, 78k miles, clean title",
        "description": "One owner, dealer serviced, new tires last year. Small dent on rear bumper. "
                       "Runs perfect — great first car or commuter.",
        "price": 6500, "original_price": 9800, "category": "cars",
        "area": "Long Island City, Queens NY", "source": "facebook",
        "source_url": "https://www.facebook.com/marketplace/item/100002",
        "image_url": None, "condition": "good",
    },
    {
        "title": "Free moving boxes + bubble wrap (about 30 boxes)",
        "description": "Just finished unpacking. ~30 sturdy boxes, mostly medium/large, plus a roll "
                       "of bubble wrap. Porch pickup any evening.",
        "price": 0, "original_price": None, "category": "free",
        "area": "Sunnyside, Queens NY", "source": "facebook",
        "source_url": "https://www.facebook.com/marketplace/item/100003",
        "image_url": None, "condition": "good",
    },
    {
        "title": "Patagonia down sweater jacket, men's M",
        "description": "Worn twice, basically new. No stains, zipper perfect. Retail is $229 — "
                       "this is the warmest jacket per dollar you'll ever find.",
        "price": 40, "original_price": 229, "category": "clothing",
        "area": "Astoria, Queens NY", "source": "instagram",
        "source_url": "https://www.instagram.com/p/100004",
        "image_url": None, "condition": "like-new",
    },
    {
        "title": "KitchenAid Artisan stand mixer, empire red",
        "description": "Works flawlessly, includes paddle, whisk, dough hook, and bowl. "
                       "Selling because we upgraded to the Pro model.",
        "price": 120, "original_price": 499, "category": "home",
        "area": "Jackson Heights, Queens NY", "source": "facebook",
        "source_url": "https://www.facebook.com/marketplace/item/100005",
        "image_url": None, "condition": "good",
    },
    {
        "title": "Sony WH-1000XM4 noise cancelling headphones",
        "description": "Best-in-class noise cancelling. Battery still holds 25+ hours. "
                       "Includes case and cables. Selling to fund the XM5s.",
        "price": 90, "original_price": 349, "category": "electronics",
        "area": "Forest Hills, Queens NY", "source": "instagram",
        "source_url": "https://www.instagram.com/p/100006",
        "image_url": None, "condition": "good",
    },
]


def main() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    from main import db as _db
    _db().close()  # ensure schema exists
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    # agents
    agents = {}
    for name in ("fren", "dealhound"):
        row = con.execute("SELECT id FROM agents WHERE display_name=?", (name,)).fetchone()
        if row:
            agents[name] = row["id"]
        else:
            cur = con.execute(
                "INSERT INTO agents (display_name, key_hash, created_at) VALUES (?,?,?)",
                (name, key_hash("seeded-" + name), now_iso()),
            )
            agents[name] = cur.lastrowid
    # deals
    for d in DEALS:
        exists = con.execute(
            "SELECT 1 FROM deals WHERE title=?", (d["title"],)
        ).fetchone()
        if exists:
            continue
        score = steal_score(d["price"], d["original_price"], d["category"])
        con.execute(
            """INSERT INTO deals (agent_id, title, description, price, original_price,
                                  steal_score, category, area, source, source_url,
                                  image_url, condition, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (agents["fren"], d["title"], d["description"], d["price"], d["original_price"],
             score, d["category"], d["area"], d["source"], d["source_url"],
             d["image_url"], d["condition"], now_iso()),
        )
    # demo verification: dealhound verifies the Aeron (deal id 1 if fresh)
    row = con.execute(
        "SELECT id FROM deals WHERE title LIKE 'Herman Miller Aeron%'"
    ).fetchone()
    if row:
        try:
            con.execute(
                "INSERT INTO verifications (deal_id, agent_id, created_at) VALUES (?,?,?)",
                (row["id"], agents["dealhound"], now_iso()),
            )
        except sqlite3.IntegrityError:
            pass
    con.commit()
    n = con.execute("SELECT COUNT(*) AS c FROM deals").fetchone()["c"]
    con.close()
    print(f"seeded: {n} deals total in {DB_PATH}")


if __name__ == "__main__":
    main()
