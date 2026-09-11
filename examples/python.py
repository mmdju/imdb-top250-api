"""Minimal example: fetch the IMDb Top 250 movies and TV shows.

Needs nothing but the Python standard library:

    python python.py
    BASE_URL=http://localhost:8000 python python.py   # against local server
"""

import json
import os
import urllib.parse
import urllib.request

BASE = os.environ.get("BASE_URL", "https://imdb-top250.mmdju.workers.dev")


def get(path):
    req = urllib.request.Request(
        BASE + path,
        headers={"User-Agent": "Mozilla/5.0 (imdb-top250-api example)"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res)


def show(title, payload, n=5):
    total = payload.get("total", payload.get("count"))
    print(f"\n{title} (showing {len(payload['data'])} of {payload['count']}, total {total}):")
    for item in payload["data"][:n]:
        print(f"  #{item['rank']:>3}  {item['title']} ({item['year']})  {item['rating']}  {item['id']}")


def show_one(title, payload):
    item = payload["data"]
    print(f"\n{title}: #{item['rank']} {item['title']} ({item['year']}) {item['rating']} {item['url']}")


if __name__ == "__main__":
    # 1. Plain lists (old way still works)
    show("Top movies", get("/top250?limit=5"))

    # 2. Search + filter + sort + pagination (new)
    q = urllib.parse.urlencode({"search": "godfather", "sort": "year", "order": "asc"})
    show("Search 'godfather'", get(f"/top250?{q}"))

    q = urllib.parse.urlencode({"min_rating": 9, "sort": "year", "order": "desc", "limit": 5})
    show("Movies rated 9+", get(f"/top250?{q}"))

    q = urllib.parse.urlencode({"year": 2008, "limit": 5})
    show("TV from 2008", get(f"/toptv?{q}"))

    # 3. Single item + random (new)
    show_one("Single movie", get("/movie/tt0111161"))
    show_one("Single TV", get("/tv/tt0903747"))
    show_one("Random pick", get("/random?type=all"))

    # 4. Use as a library (no HTTP needed, same logic as the server).
    #     Run from inside python/:
    #     from app import query_chart, get_by_id, get_random
    #     query_chart("top250", search="nolan", min_rating=8, limit=3)
