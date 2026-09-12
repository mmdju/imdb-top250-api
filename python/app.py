"""IMDb Top 250 API - Python version (FastAPI).

Same API as the Cloudflare Worker in ../src, for those who'd rather
self-host with Python:

    pip install -r requirements.txt
    uvicorn app:app --host 0.0.0.0 --port 8000
    # or: docker build -t imdb-top250-api . && docker run -p 8000:8000 imdb-top250-api

Endpoints: /top250, /toptv (with search/filter/sort/pagination),
/movie/{imdb_id}, /tv/{imdb_id}, /random, /refresh (admin), /stats, / (help).

Use as a library in your own project:

    from app import query_chart, get_by_id, get_random

    top = query_chart("top250", search="godfather", min_rating=8.5, limit=5)
    one = get_by_id("top250", "tt0111161")
    lucky = get_random("all")

Config via environment (.env supported if python-dotenv is installed):
    JINA_API_KEY=...  # optional but recommended, makes live fetch reliable
    ADMIN_KEY=...     # required to enable /refresh (fail-closed without it)
"""

import hmac
import html
import json
import os
import random
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

HERE = Path(__file__).parent
SEED_DIR = HERE.parent / "src"
DAY = 24 * 3600

try:  # optional: load python/.env for local dev
    from dotenv import load_dotenv

    load_dotenv(HERE / ".env")
except ImportError:
    pass

JINA_API_KEY = os.environ.get("JINA_API_KEY", "").strip()
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

VALID_SORTS = ("rank", "rating", "year", "votes", "title")


def admin_key_from(request: Request, key: str) -> str:
    """Key via `Authorization: Bearer <key>` (preferred) or `?key=` fallback."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return key or ""


def check_admin(key: str):
    """Fail closed: /refresh is unavailable until ADMIN_KEY is configured."""
    if not ADMIN_KEY:
        return JSONResponse(
            {"error": "Refresh unavailable (ADMIN_KEY not configured)"}, status_code=503
        )
    if not hmac.compare_digest(key or "", ADMIN_KEY):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return None


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
        known = path in ("/", "/top250", "/toptv", "/movie", "/tv", "/random", "/refresh", "/stats")
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
            "total": len(data),
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
            "total": len(chart["seed"]),
            "stale": True,
            "fallback": True,
            "liveError": str(err),
            "note": "Live fetch failed. Serving bundled seed data.",
            "data": chart["seed"],
        }


def default_order(sort: str) -> str:
    return "asc" if sort in ("rank", "title") else "desc"


def query_chart(
    name: str,
    search: str = "",
    year: Optional[int] = None,
    min_rating: Optional[float] = None,
    sort: str = "rank",
    order: Optional[str] = None,
    offset: int = 0,
    limit: int = 250,
    force: bool = False,
) -> dict:
    """Library-friendly query: filter + sort + paginate a chart.

    Mirrors the JS `listResponse()` in ../src/index.js so both versions
    behave identically. `name` is "top250" or "toptv".
    Returns the public payload dict (same shape as the HTTP response).
    """
    if name not in CHARTS:
        raise ValueError('name must be "top250" or "toptv"')
    sort = (sort or "rank").lower()
    if sort not in VALID_SORTS:
        sort = "rank"
    order = (order or default_order(sort)).lower()
    if order not in ("asc", "desc"):
        order = default_order(sort)
    limit = min(max(int(limit or 250), 1), 250)
    offset = max(int(offset or 0), 0)
    search = (search or "").strip()

    result = get_chart(name, force=force)
    total = len(result["data"])
    filtered = result["data"]
    if search:
        q = search.lower()
        filtered = [e for e in filtered if q in (e.get("title") or "").lower()]
    if year is not None:
        filtered = [e for e in filtered if e.get("year") == year]
    if min_rating is not None:
        filtered = [e for e in filtered if (e.get("rating") if e.get("rating") is not None else -1) >= min_rating]
    count = len(filtered)

    def sort_key(e):
        if sort == "title":
            return (e.get("title") or "").lower()
        v = e.get(sort)
        # None values sort last regardless of direction.
        return (v is None, v)

    reverse = order == "desc"
    # For None-last with reverse we sort in two steps.
    if sort == "title":
        ordered = sorted(filtered, key=sort_key, reverse=reverse)
    else:
        not_none = sorted([e for e in filtered if e.get(sort) is not None], key=lambda e: e.get(sort), reverse=reverse)
        nones = [e for e in filtered if e.get(sort) is None]
        ordered = not_none + nones

    page = ordered[offset : offset + limit]
    out = {k: v for k, v in result.items() if k != "fetched_at"}
    out.update(
        {
            "total": total,
            "count": count,
            "offset": offset,
            "limit": limit,
            "filters": {
                "search": search or None,
                "year": year,
                "min_rating": min_rating,
                "sort": sort,
                "order": order,
            },
            "data": page,
        }
    )
    return out


def get_by_id(name: str, imdb_id: str, force: bool = False) -> Optional[dict]:
    """Library-friendly single lookup. Returns the item dict or None."""
    result = get_chart(name, force=force)
    for e in result["data"]:
        if e.get("id") == imdb_id:
            return {"meta": {k: v for k, v in result.items() if k != "data"}, "data": e}
    return None


def get_random(kind: str = "all", force: bool = False) -> dict:
    """Library-friendly random pick. kind=movie|tv|all. Returns {type, meta, data}."""
    kind = (kind or "all").lower()
    if kind not in ("movie", "tv", "all"):
        raise ValueError("kind must be movie|tv|all")
    pick = random.choice(["movie", "tv"]) if kind == "all" else kind
    name = "top250" if pick == "movie" else "toptv"
    result = get_chart(name, force=force)
    if not result["data"]:
        raise RuntimeError("Empty chart")
    return {
        "type": pick,
        "meta": {k: v for k, v in result.items() if k != "data"},
        "data": random.choice(result["data"]),
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


app = FastAPI(
    title="IMDb Top 250 API",
    description="Clean JSON API for IMDb Top 250 movies and TV shows (facts only). Same shape as the Cloudflare Worker version.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


def public_payload(result, limit):
    # Back-compat helper (old callers). Prefer query_chart() for new code.
    data = {k: v for k, v in result.items() if k != "fetched_at"}
    data["data"] = result["data"][:limit]
    return data


ChartSort = Literal["rank", "rating", "year", "votes", "title"]
ChartOrder = Literal["asc", "desc"]


@app.get("/top250", summary="Top 250 movies")
@app.get("/toptv", summary="Top 250 TV shows")
def chart(
    request: Request,
    limit: int = Query(250, ge=1, le=250, description="How many to return"),
    offset: int = Query(0, ge=0, description="Skip N after filtering/sorting"),
    search: str = Query("", description="Case-insensitive substring on title"),
    year: Optional[int] = Query(None, ge=1800, le=2100, description="Exact year match"),
    min_rating: Optional[float] = Query(None, ge=0, le=10, description="Keep rating >= this"),
    sort: ChartSort = Query("rank", description="Sort field"),
    order: Optional[ChartOrder] = Query(None, description="asc|desc (default smart)"),
):
    path = request.url.path
    record_hit(path)
    name = path.lstrip("/")
    return query_chart(
        name,
        search=search,
        year=year,
        min_rating=min_rating,
        sort=sort,
        order=order,
        offset=offset,
        limit=limit,
    )


@app.get("/movie/{imdb_id}", summary="Single movie by IMDb id")
def movie_by_id(imdb_id: str):
    record_hit("/movie")
    if not re.fullmatch(r"tt\d{1,10}", imdb_id):
        return JSONResponse({"error": "Invalid IMDb id. Use like /movie/tt0111161"}, status_code=400)
    found = get_by_id("top250", imdb_id)
    if not found:
        return JSONResponse({"error": "Not found", "id": imdb_id}, status_code=404)
    meta, item = found["meta"], found["data"]
    return {
        "source": meta.get("source"),
        "updatedAt": meta.get("updatedAt"),
        "stale": meta.get("stale", True),
        **({"fallback": True, "note": meta.get("note")} if meta.get("fallback") else {}),
        "type": "movie",
        "data": item,
    }


@app.get("/tv/{imdb_id}", summary="Single TV show by IMDb id")
def tv_by_id(imdb_id: str):
    record_hit("/tv")
    if not re.fullmatch(r"tt\d{1,10}", imdb_id):
        return JSONResponse({"error": "Invalid IMDb id. Use like /tv/tt0903747"}, status_code=400)
    found = get_by_id("toptv", imdb_id)
    if not found:
        return JSONResponse({"error": "Not found", "id": imdb_id}, status_code=404)
    meta, item = found["meta"], found["data"]
    return {
        "source": meta.get("source"),
        "updatedAt": meta.get("updatedAt"),
        "stale": meta.get("stale", True),
        **({"fallback": True, "note": meta.get("note")} if meta.get("fallback") else {}),
        "type": "tv",
        "data": item,
    }


@app.get("/random", summary="Random title")
def random_title(type: str = Query("all", description="movie|tv|all")):
    record_hit("/random")
    kind = (type or "all").lower()
    if kind not in ("movie", "tv", "all"):
        return JSONResponse({"error": "Invalid type. Use ?type=movie|tv|all"}, status_code=400)
    try:
        picked = get_random(kind)
    except RuntimeError as err:
        return JSONResponse({"error": str(err)}, status_code=503)
    meta = picked["meta"]
    return {
        "source": meta.get("source"),
        "updatedAt": meta.get("updatedAt"),
        "stale": meta.get("stale", True),
        **({"fallback": True, "note": meta.get("note")} if meta.get("fallback") else {}),
        "type": picked["type"],
        "data": picked["data"],
    }


@app.get("/refresh")
def refresh(request: Request, key: str = ""):
    record_hit("/refresh")
    denied = check_admin(admin_key_from(request, key))
    if denied is not None:
        return denied
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
<body style="font-family:sans-serif;max-width:680px;margin:40px auto;line-height:1.7">
<h1>IMDb Top 250 API</h1>
<p>Clean JSON served from Python. Movies: <code>imdb.com/chart/top</code>, TV shows: <code>imdb.com/chart/toptv</code> (facts only).</p>
<ul>
<li><code>GET /top250</code> - top 250 movies</li>
<li><code>GET /toptv</code> - top 250 TV shows</li>
<li><code>GET /movie/tt0111161</code> - single movie</li>
<li><code>GET /tv/tt0903747</code> - single TV show</li>
<li><code>GET /random?type=all</code> - random item</li>
</ul>
<p><b>Filters:</b> <code>?search=godfather&year=1972&min_rating=9&sort=year&order=desc&limit=10&offset=0</code></p>
<p>Docs: <a href="/docs">/docs</a> - OpenAPI auto-generated.</p>
</body></html>"""
