# IMDb Top 250 API

Clean JSON API for the IMDb Top 250, read live from
`https://www.imdb.com/chart/top/` through the Jina Reader proxy
(`r.jina.ai`, because IMDb blocks bots/datacenter IPs with AWS WAF)
and served from Cloudflare Workers with edge caching.

## Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Help page |
| `GET` | `/top250` | Full Top 250 list (cached, refreshed max once a day) |
| `GET` | `/top250?limit=10` | First N titles |

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

## Deploy

```bash
npx wrangler kv namespace create CACHE
# put the returned id into wrangler.toml

npx wrangler d1 create imdb-top250-db
# put the binding/id into wrangler.toml, then:
npx wrangler d1 migrations apply imdb-top250-db --remote

npx wrangler secret put ADMIN_KEY   # optional, protects /refresh
npx wrangler secret put JINA_API_KEY  # optional but recommended, makes live fetch reliable
npx wrangler deploy
```

## Admin endpoints

These are not listed on the `/` help page, but they work:

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/refresh?key=ADMIN_KEY` | Force a fresh fetch (needs the key only if `ADMIN_KEY` is set) |
| `GET` | `/stats` | Total request counts, backed by D1 |

## Notes

- IMDb blocks bots/datacenter IPs (AWS WAF challenge), so the live chart is
  read through the Jina Reader proxy. Without a `JINA_API_KEY` it still works
  but can be rate-limited; with the free key it is far more reliable.
- If the live fetch fails, the API serves the last good cached copy with
  `"stale": true`, otherwise the bundled seed `src/fallback.json`
  (chart snapshot 2026-09-01).
- Live rank/title/year/rating/votes come from the chart; IMDb ids come from
  the chart links, with the seed list as backup for anything unmatched.

## License

MIT — see [LICENSE](LICENSE).
