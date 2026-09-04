"""IMDb Top 250 API — Python version (FastAPI).

Same API as the Cloudflare Worker in ../src, for those who'd rather
self-host with Python:

    pip install -r requirements.txt
    uvicorn app:app

Endpoints: /top250, /toptv, /refresh (admin), /stats, / (help).
"""

import html
import json
import os
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

HERE = Path(__file__).parent
SEED_DIR = HERE.parent / "src"
DAY = 24 * 3600

JINA_API_KEY = os.environ.get("JINA_API_KEY", "").strip()
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")


def load_seed(name):
    with open(SEED_DIR / name, encoding="utf-8") as f:
        return json.load(f)


CHARTS = {
    "top250": {
        "jina": "https://r.jina.ai/https://www.imdb.com/chart/top/",
        "seed": load_seed("fallback.json"),
        "seed_updated_at": "2026-09-01T00:00:00.000Z",
        "seed_source": "seed: movies chart snapshot 2026-09-01 (facts only; ids partial)",
        "live_source": "live: r.jina.ai proxy of imdb.com/chart/top",
    },
    "toptv": {
        "jina": "https://r.jina.ai/https://www.imdb.com/chart/toptv/",
        "seed": load_seed("fallback-tv.json"),
        "seed_updated_at": "2026-09-04T00:00:00.000Z",
        "seed_source": "seed: tv chart snapshot 2026-09-04 (facts only)",
        "live_source": "live: r.jina.ai proxy of imdb.com/chart/toptv",
    },
}

# In-memory cache: {name: payload}. Lost on restart, rebuilt on demand.
cache = {}

DB_PATH = HERE / "hits.db"


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS hits (key TEXT PRIMARY KEY, count INTEGER)")
    return con


def record_hit(path):
    try:
        known = path in ("/", "/top250", "/toptv", "/refresh", "/stats")
        key = path if known else "other"
        con = db()
        con.execute(
            "INSERT INTO hits(key, count) VALUES ('total', 1) "
            "ON CONFLICT(key) DO UPDATE SET count = count + 1"
        )
        con.execute(
            "INSERT INTO hits(key, count) VALUES (?, 1) "
            "ON CONFLICT(key) DO UPDATE SET count = count + 1",
            (key,),
        )
        con.commit()
        con.close()
    except Exception:
        pass  # stats never break the API


def read_stats():
    out = {"total": 0, "by_path": {}}
    try:
        con = db()
        for key, count in con.execute("SELECT key, count FROM hits"):
            if key == "total":
                out["total"] = count
            else:
                out["by_path"][key] = count
        con.close()
    except Exception:
        pass
    return out


def get_chart(name, force=False):
    """Cached chart when fresh, else live, else last good copy, else seed."""
    chart = CHARTS[name]
    if not force and name in cache:
        item = cache[name]
        if time.time() - item["fetched_at"] < DAY:
            return {**item, "stale": False}
    try:
        data = fetch_live_chart(chart)
        payload = {
            "source": chart["live_source"],
            "updatedAt": now_iso(),
            "fetched_at": time.time(),
            "count": len(data),
            "stale": False,
            "data": data,
        }
        cache[name] = payload
        return payload
    except Exception as err:
        if name in cache:
            return {**cache[name], "stale": True}
        return {
            "source": chart["seed_source"],
            "updatedAt": chart["seed_updated_at"],
            "fetched_at": time.time(),
            "count": len(chart["seed"]),
            "stale": True,
            "fallback": True,
            "liveError": str(err),
            "note": "Live fetch failed. Serving bundled seed data.",
            "data": chart["seed"],
        }


def fetch_live_chart(chart):
    headers = {
        "X-With-Images-Summary": "false",
        # Cloudflare 403s urllib's default UA, so identify as a browser.
        "User-Agent": "Mozilla/5.0 (imdb-top250-api)",
    }
    if JINA_API_KEY:
        headers["Authorization"] = "Bearer " + JINA_API_KEY
    req = urllib.request.Request(chart["jina"], headers=headers)
    with urllib.request.urlopen(req, timeout=60) as res:
        if res.status != 200:
            raise RuntimeError(f"Chart proxy responded with HTTP {res.status}")
        text = res.read().decode("utf-8", "replace")
    if not text or re.search(
        r"AwsWafIntegration|window\.gokuProps|challenge\.js|Just a moment|Verify you are human",
        text,
        re.I,
    ):
        raise RuntimeError("Chart proxy returned a bot-check page instead of the chart")
    live = parse_jina_chart(text)
    if len(live) < 200:
        raise RuntimeError(f"Only parsed {len(live)} titles from live chart (need 200+)")
    return enrich_with_ids(live, chart["seed"])


def parse_jina_chart(text):
    """Jina sends one of two layouts: rich (keyed) or plain (keyless)."""
    heads = list(re.finditer(r"#(\d{1,3})\s*####\s*\[([^\]]+)\]\(([^)]+)\)", text))
    if len(heads) >= 200:
        entries, seen = [], set()
        for i, h in enumerate(heads):
            rank = int(h.group(1))
            if not 1 <= rank <= 250 or rank in seen:
                continue
            seen.add(rank)
            end = heads[i + 1].start() if i + 1 < len(heads) else h.start() + 800
            chunk = text[h.start() : end]
            idm = re.search(r"/title/(tt\d+)", h.group(3))
            ym = re.search(r"\*\s*(\d{4})", chunk)
            rm = re.search(r"(\d{1,2}\.\d)\s*\(([\d.,]+)\s*([MK])?\)", chunk)
            entries.append(
                {
                    "rank": rank,
                    "title": h.group(2).strip(),
                    "id": idm.group(1) if idm else None,
                    "year": int(ym.group(1)) if ym else None,
                    "rating": float(rm.group(1)) if rm else None,
                    "votes": parse_compact_votes(rm.group(2), rm.group(3)) if rm else None,
                }
            )
        entries.sort(key=lambda e: e["rank"])
        if len(entries) >= 200:
            return entries

    marks = []
    for m in re.finditer(r"#(\d{1,3})\s*\n+([^#\n]+?)\s*\n+(\d{4})", text):
        rank = int(m.group(1))
        if 1 <= rank <= 250:
            marks.append(
                {"rank": rank, "title": m.group(2).strip(), "year": int(m.group(3)),
                 "end": m.end(), "at": m.start()}
            )
    seen, entries = set(), []
    for e in marks:
        if e["rank"] not in seen:
            seen.add(e["rank"])
            entries.append(e)
    entries.sort(key=lambda e: e["rank"])
    for i, e in enumerate(entries):
        to = entries[i + 1]["at"] if i + 1 < len(entries) else e["end"] + 400
        rm = re.search(r"(\d{1,2}\.\d)\s*\n+\s*\(([\d.,]+)\s*([MK])?\)", text[e["end"] : to])
        e["rating"] = float(rm.group(1)) if rm else None
        e["votes"] = parse_compact_votes(rm.group(2), rm.group(3)) if rm else None
        e["id"] = None
        del e["end"]
        del e["at"]
    return entries


def parse_compact_votes(num, suffix):
    try:
        n = float(str(num).replace(",", ""))
    except ValueError:
        return None
    if suffix == "M":
        return round(n * 1e6)
    if suffix == "K":
        return round(n * 1e3)
    return round(n)


def enrich_with_ids(live, seed):
    """Fill gaps (missing ids/ratings/votes) from the seed, matched by title."""
    by_title = {}
    for s in seed:
        key = norm_title(s["title"])
        if key and key not in by_title:
            by_title[key] = s
    out = []
    for e in live:
        s = by_title.get(norm_title(e["title"]), {})
        imdb_id = e["id"] or s.get("id")
        out.append(
            {
                "rank": e["rank"],
                "id": imdb_id,
                "title": e["title"],
                "year": e["year"],
                "rating": e["rating"] if e["rating"] is not None else s.get("rating"),
                "votes": e["votes"] if e["votes"] is not None else s.get("votes"),
                "url": f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else None,
            }
        )
    return out


def norm_title(s):
    return re.sub(r"[^a-z0-9]+", "", html.unescape(s or "").lower())


app = FastAPI(title="IMDb Top 250 API")


def public_payload(result, limit):
    data = {k: v for k, v in result.items() if k != "fetched_at"}
    data["data"] = result["data"][:limit]
    return data


@app.get("/top250")
@app.get("/toptv")
def chart(request: Request, limit: int = Query(250, ge=1, le=250)):
    path = request.url.path
    record_hit(path)
    name = path.lstrip("/")
    return public_payload(get_chart(name), limit)


@app.get("/refresh")
def refresh(request: Request, key: str = ""):
    record_hit("/refresh")
    if ADMIN_KEY and key != ADMIN_KEY:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    out = {}
    for name in CHARTS:
        result = get_chart(name, force=True)
        if result.get("fallback"):
            out[name] = {
                "error": "Refresh failed",
                "detail": result.get("liveError", "Live fetch failed"),
                "fallback": True,
                "note": result.get("note"),
                "count": len(result["data"]),
            }
        else:
            out[name] = {
                "refreshed": True,
                "count": len(result["data"]),
                "updatedAt": result["updatedAt"],
            }
    return out


@app.get("/stats")
def stats():
    record_hit("/stats")
    s = read_stats()
    return {
        "total_requests": s["total"],
        "by_path": s["by_path"],
        "updatedAt": now_iso(),
    }


@app.get("/", response_class=HTMLResponse)
def help_page(request: Request):
    record_hit("/")
    return """<!doctype html><html><head><meta charset="utf-8"><title>IMDb Top 250 API</title></head>
<body style="font-family:sans-serif;max-width:640px;margin:40px auto;line-height:1.7">
<h1>IMDb Top 250 API</h1>
<p>Clean JSON served from the edge. Movies: <code>imdb.com/chart/top</code>, TV shows: <code>imdb.com/chart/toptv</code> (facts only).</p>
<ul>
<li><code>GET /top250</code> — top 250 movies</li>
<li><code>GET /top250?limit=10</code> — first 10 movies</li>
<li><code>GET /toptv</code> — top 250 TV shows</li>
<li><code>GET /toptv?limit=10</code> — first 10 shows</li>
</ul>
</body></html>"""
