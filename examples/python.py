"""Minimal example: fetch the IMDb Top 250 movies and TV shows.

Needs nothing but the Python standard library:

    python python.py
"""

import json
import urllib.request

BASE = "https://tmdb-top250.codepions.workers.dev"


def get(path):
    req = urllib.request.Request(
        BASE + path,
        headers={"User-Agent": "Mozilla/5.0 (imdb-top250-api example)"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res)


def show(title, payload, n=5):
    print(f"\n{title} (showing {n} of {payload['count']}):")
    for item in payload["data"][:n]:
        print(f"  #{item['rank']:>3}  {item['title']} ({item['year']})  {item['rating']}")


if __name__ == "__main__":
    show("Top movies", get("/top250?limit=250"))
    show("Top TV shows", get("/toptv?limit=250"))
