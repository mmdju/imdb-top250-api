# IMDb Top 250 API

![IMDb Top 250 API banner](assets/imdb-top250.png)

[![CI](https://github.com/mmdju/imdb-top250-api/actions/workflows/ci.yml/badge.svg)](https://github.com/mmdju/imdb-top250-api/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Cloudflare Workers](https://img.shields.io/badge/Cloudflare-Workers-F38020?logo=cloudflare&logoColor=white)](https://workers.cloudflare.com/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/mmdju/imdb-top250-api/pulls)

Clean JSON API for the IMDb Top 250 movies and Top 250 TV shows, read live from
`https://www.imdb.com/chart/top/` and `https://www.imdb.com/chart/toptv/`
through the Jina Reader proxy
(`r.jina.ai`, because IMDb blocks bots/datacenter IPs with AWS WAF)
and served from Cloudflare Workers with edge caching.

**No deploy needed - use the hosted API right now:**

Base URL: `https://imdb-top250.mmdju.workers.dev`

- Movies, full list: [*/top250*](https://imdb-top250.mmdju.workers.dev/top250)
- Movies, first 10: [*/top250?limit=10*](https://imdb-top250.mmdju.workers.dev/top250?limit=10)
- TV shows, full list: [*/toptv*](https://imdb-top250.mmdju.workers.dev/toptv)
- TV shows, first 10: [*/toptv?limit=10*](https://imdb-top250.mmdju.workers.dev/toptv?limit=10)

Just open the links - no key, no setup. (Deploy your own copy only if you want
your own cache and stats - see below.)

## Features

- Live Top 250 movies + Top 250 TV shows (rank, title, year, rating, votes, IMDb link)
- Edge cached (refreshed max once a day), stale-while-revalidate style fallback
- Bundled seed data so the API answers even when the live fetch is down
- Search, filter, sort and pagination on both lists
- Single-title lookup (`/movie/:id`, `/tv/:id`) and `/random`
- Request counters backed by D1 (`/stats`)
- Facts only - no posters, plots or images

## Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Help page |
| `GET` | `/top250` | Full Top 250 movies list (cached, refreshed max once a day) |
| `GET` | `/top250?limit=10` | First N movies |
| `GET` | `/toptv` | Full Top 250 TV shows list (cached, refreshed max once a day) |
| `GET` | `/toptv?limit=10` | First N shows |
| `GET` | `/movie/tt0111161` | Single movie by IMDb id (`404` if missing) |
| `GET` | `/tv/tt0903747` | Single TV show by IMDb id (`404` if missing) |
| `GET` | `/random?type=all` | Random item, `type=movie\|tv\|all` (default `all`) |

List filters (work on `/top250` and `/toptv`, can be combined):

| Param | Example | Description |
| :--- | :--- | :--- |
| `search` | `?search=godfather` | Title contains, case-insensitive |
| `year` | `?year=1994` | Exact year match |
| `min_rating` | `?min_rating=8.5` | Keep `rating >= value` |
| `sort` | `?sort=rating` | `rank\|rating\|year\|votes\|title` (default `rank`) |
| `order` | `?order=desc` | `asc\|desc` (default: `rank`/`title` asc, rest desc) |
| `limit` | `?limit=10` | `1..250`, default `250` |
| `offset` | `?offset=20` | Skip N after filtering/sorting, default `0` |

Combined example: `/top250?search=the&min_rating=9&sort=year&order=desc&limit=5&offset=0`

Example item:

```json
{
  "rank": 1,
  "id": "tt0111161",
  "title": "The Shawshank Redemption",
  "year": 1994,
  "rating": 9.3,
  "votes": 2665388,
  "url": "https://www.imdb.com/title/tt0111161/"
}
```

Only factual fields are served (title, year, rating, votes).
No posters, plots or images - those belong to their copyright holders.

List responses wrap the items with paging info:

```json
{
  "source": "live: r.jina.ai proxy of imdb top",
  "updatedAt": "2026-09-11T04:06:35.401Z",
  "stale": false,
  "total": 250,
  "count": 2,
  "offset": 0,
  "limit": 250,
  "filters": { "search": "godfather", "year": null, "sort": "rank", "order": "asc" },
  "data": [ { "rank": 1, "...": "..." }, { "rank": 2, "...": "..." } ]
}
```

## Examples

- [Python](examples/python.py) - standard library only, no install needed:

```bash
python examples/python.py
# against your local server (PowerShell):
#   $env:BASE_URL="http://localhost:8000"; python examples/python.py
# against your local server (bash):
#   BASE_URL=http://localhost:8000 python examples/python.py
```

## Python version

Prefer self-hosting with Python? `python/` is the same API on FastAPI:

```bash
cd python
pip install -r requirements.txt
uvicorn app:app        # local test at http://localhost:8000/toptv
# Docs (Swagger): http://localhost:8000/docs
```
```bash
# Docker (run from the repo root, not from python/):
docker build -f python/Dockerfile -t imdb-top250-api . && docker run -p 8000:8000 imdb-top250-api
```

Same endpoints and response shape. Config via environment (see `python/.env.example`):

```bash
JINA_API_KEY=... ADMIN_KEY=... uvicorn app:app --host 0.0.0.0 --port 8000
```

Use it as a library in your own project (no HTTP needed, run from inside `python/`):

```python
from app import query_chart, get_by_id, get_random

query_chart("top250", search="godfather", min_rating=8.5, limit=5)
get_by_id("top250", "tt0111161")
get_random("all")  # movie | tv | all
```

Seed data is shared with the Worker (`../src/fallback*.json`);
request counters live in a local SQLite file (`hits.db`).

## Test on your PC

```bash
npm install
npx wrangler dev        # local test at http://localhost:8787/top250
```

KV works locally with emulation, no setup needed for a first test.
(Windows: if `npx` is blocked by the execution policy, run `npx.cmd wrangler dev` instead.)

## Deploy

```bash
npx wrangler kv namespace create CACHE
# put the returned id into wrangler.toml

npx wrangler d1 create imdb-top250-db
# put the binding/id into wrangler.toml, then:
npx wrangler d1 migrations apply imdb-top250-db --remote

npx wrangler secret put ADMIN_KEY   # required to enable /refresh (fail-closed without it)
npx wrangler secret put JINA_API_KEY  # optional but recommended, makes live fetch reliable
npx wrangler deploy
```

## Admin endpoints

These are not listed on the `/` help page, but they work:

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/refresh` | Force a fresh fetch of both charts. Admin-only: send `Authorization: Bearer ADMIN_KEY` (or `?key=` fallback). Returns `503` until `ADMIN_KEY` is configured |
| `GET` | `/stats` | Total request counts, backed by D1 (public) |

## Notes

- IMDb blocks bots/datacenter IPs (AWS WAF challenge), so the live chart is
  read through the Jina Reader proxy. Without a `JINA_API_KEY` it still works
  but can be rate-limited; with the free key it is far more reliable.
- If the live fetch fails, the API serves the last good cached copy with
  `"stale": true`, otherwise the bundled seeds (`src/fallback.json` for movies,
  chart snapshot 2026-09-01; `src/fallback-tv.json` for TV, snapshot 2026-09-04).
- Live rank/title/year/rating/votes come from the chart; IMDb ids come from
  the chart links, with the seed list as backup for anything unmatched.

## License

MIT - see [LICENSE](LICENSE).

## Project structure

```
src/index.js        worker (JS version): routes, live fetch, parsing, cache, stats
src/sort.mjs        pure list-sort comparator (nulls last, both directions)
tests/sort.test.mjs unit tests for the comparator (`npm test`)
src/fallback.json   seed data, top 250 movies (250 titles, facts only)
src/fallback-tv.json  seed data, top 250 TV shows (250 titles, facts only)
python/app.py       same API in Python (FastAPI), self-hostable + importable (query_chart/get_by_id/get_random)
python/requirements.txt  Python dependencies (pinned)
python/Dockerfile   container for the Python version
python/.env.example sample config (JINA_API_KEY, ADMIN_KEY)
examples/python.py  tiny client example (stdlib only)
migrations/         D1 schema for the request counters
wrangler.toml       Worker, KV and D1 config
```
