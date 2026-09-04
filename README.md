# IMDb Top 250 API

Clean JSON API for the IMDb Top 250, scraped live from
`https://www.imdb.com/chart/top/` and served from Cloudflare Workers
with edge caching.

> Status: **private / work in progress.** Do not make public yet.

## Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Help page |
| `GET` | `/top250` | Full Top 250 list (cached, refreshed max once a day) |
| `GET` | `/top250?limit=10` | First N titles |
| `GET` | `/refresh?key=ADMIN_KEY` | Force a fresh scrape |

Example item:

```json
{
  "rank": 1,
  "id": "tt0111161",
  "title": "The Shawshank Redemption",
  "year": 1994,
  "rating": 9.3,
  "votes": 2800000,
  "url": "https://www.imdb.com/title/tt0111161/"
}
```

Only factual fields are served (title, year, rating, votes).
No posters, plots or images — those belong to their copyright holders.

## Test on your PC

```bash
npm install
npx wrangler dev        # local test at http://localhost:8787/top250
```

KV works locally with emulation, no setup needed for a first test.

## Deploy (later)

```bash
npx wrangler kv namespace create CACHE
# put the returned id into wrangler.toml

npx wrangler secret put ADMIN_KEY   # optional, protects /refresh
npx wrangler deploy
```

## Notes

- IMDb sometimes blocks datacenter IPs. If a live scrape fails, the API
  serves the last good cached copy with `"stale": true`.
- If the page layout changes and parsing breaks, `scrapeChart()` throws
  and the error tells you what happened — check `/refresh` output.
