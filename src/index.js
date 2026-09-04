/**
 * IMDb Top 250 — clean JSON API on Cloudflare Workers.
 *
 * Data source: https://www.imdb.com/chart/top/ (scraped, embedded page data).
 * Only factual fields are served (rank, title, year, rating, votes, link).
 * No posters, plots or images — those belong to their copyright holders.
 *
 * Endpoints:
 *   GET /              -> this help page
 *   GET /top250        -> full list (cached, refreshed at most once a day)
 *   GET /top250?limit=10
 *   GET /refresh       -> force a fresh scrape (needs ?key=ADMIN_KEY when set)
 */

const CHART_URL = "https://www.imdb.com/chart/top/";
const CACHE_KEY = "top250:v1";
const CACHE_TTL_SECONDS = 24 * 3600;

const BROWSER_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/top250") {
      const limit = Math.min(
        Math.max(parseInt(url.searchParams.get("limit") || "250", 10) || 250, 1),
        250
      );
      try {
        const result = await getTop250(env, false);
        return json({ ...result, data: result.data.slice(0, limit) });
      } catch (err) {
        return json({ error: "Failed to fetch chart", detail: String(err) }, 503);
      }
    }

    if (url.pathname === "/refresh") {
      if (env.ADMIN_KEY) {
        const key = url.searchParams.get("key") || "";
        if (key !== env.ADMIN_KEY) return json({ error: "Unauthorized" }, 401);
      }
      try {
        const result = await getTop250(env, true);
        return json({ refreshed: true, count: result.data.length, updatedAt: result.updatedAt });
      } catch (err) {
        return json({ error: "Refresh failed", detail: String(err) }, 503);
      }
    }

    return new Response(helpHtml(), {
      headers: { "content-type": "text/html; charset=utf-8" },
    });
  },
};

/** Return cached chart, or scrape + cache when empty/stale/forced. */
async function getTop250(env, force) {
  if (!force && env.CACHE) {
    const cached = await env.CACHE.get(CACHE_KEY, "json");
    if (cached && Date.now() - cached.fetchedAt < CACHE_TTL_SECONDS * 1000) {
      return { ...cached, stale: false };
    }
  }

  try {
    const data = await scrapeChart();
    const payload = {
      source: "imdb.com/chart/top",
      updatedAt: new Date().toISOString(),
      fetchedAt: Date.now(),
      count: data.length,
      stale: false,
      data,
    };
    if (env.CACHE) {
      await env.CACHE.put(CACHE_KEY, JSON.stringify(payload), {
        expirationTtl: CACHE_TTL_SECONDS * 7, // keep last good copy for a week
      });
    }
    return payload;
  } catch (err) {
    // Live scrape failed (IMDb may block datacenter IPs): serve last good copy.
    if (env.CACHE) {
      const cached = await env.CACHE.get(CACHE_KEY, "json");
      if (cached) return { ...cached, stale: true };
    }
    throw err;
  }
}

/** Scrape imdb.com/chart/top and return a clean array. */
async function scrapeChart() {
  const res = await fetch(CHART_URL, {
    headers: {
      "User-Agent": BROWSER_UA,
      "Accept-Language": "en-US,en;q=0.9",
      Accept: "text/html,application/xhtml+xml",
    },
  });
  if (!res.ok) throw new Error("IMDb responded with HTTP " + res.status);
  const html = await res.text();

  const m = html.match(
    /<script id="__NEXT_DATA__" type="application\/json">([\s\S]+?)<\/script>/
  );
  if (!m) throw new Error("Embedded chart data not found (page layout changed?)");

  const next = JSON.parse(m[1]);
  const found = [];
  collectTitles(next, found);

  // De-dupe by IMDb id, keep chart order.
  const seen = new Set();
  const clean = [];
  for (const item of found) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    clean.push({
      rank: clean.length + 1,
      id: item.id,
      title: item.title,
      year: item.year,
      rating: item.rating,
      votes: item.votes,
      url: "https://www.imdb.com/title/" + item.id + "/",
    });
    if (clean.length >= 250) break;
  }
  if (clean.length < 100) {
    throw new Error("Only parsed " + clean.length + " titles (page layout changed?)");
  }
  return clean;
}

/** Recursively find title objects shaped like IMDb's chart data. */
function collectTitles(node, out) {
  if (!node || typeof node !== "object") return;
  if (Array.isArray(node)) {
    for (const item of node) collectTitles(item, out);
    return;
  }
  const id = node.id;
  const title = node.titleText && node.titleText.text;
  const rating = node.ratingsSummary;
  if (
    typeof id === "string" &&
    /^tt\d+$/.test(id) &&
    typeof title === "string" &&
    rating &&
    typeof rating.aggregateRating === "number"
  ) {
    out.push({
      id,
      title,
      year: (node.releaseYear && node.releaseYear.year) || null,
      rating: rating.aggregateRating,
      votes: rating.voteCount ?? null,
    });
  }
  for (const key of Object.keys(node)) collectTitles(node[key], out);
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      "cache-control": "public, max-age=3600",
    },
  });
}

function helpHtml() {
  return `<!doctype html><html><head><meta charset="utf-8"><title>IMDb Top 250 API</title></head>
<body style="font-family:sans-serif;max-width:640px;margin:40px auto;line-height:1.7">
<h1>IMDb Top 250 API</h1>
<p>Clean JSON served from the edge. Data: <code>imdb.com/chart/top</code> (facts only).</p>
<ul>
<li><code>GET /top250</code> — full list</li>
<li><code>GET /top250?limit=10</code> — first 10</li>
<li><code>GET /refresh?key=ADMIN_KEY</code> — force refresh</li>
</ul>
</body></html>`;
}
