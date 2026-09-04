// IMDb Top 250 as clean JSON, served from Cloudflare Workers.
//
// The chart is read through the Jina Reader proxy because IMDb blocks
// bots and datacenter IPs. Only facts are served (rank, title, year,
// rating, votes, link) — no posters, plots or images.
//
//   GET /               help page
//   GET /top250         full list, cached up to a day
//   GET /top250?limit=  first N titles
//   GET /refresh        force a fresh fetch (admin)
//   GET /stats          request counters (admin)

import FALLBACK_LIST from "./fallback.json";

const JINA_URL = "https://r.jina.ai/https://www.imdb.com/chart/top/";
const CACHE_KEY = "top250:v1";
const DAY = 24 * 3600;

// Served when the live fetch fails and there is no cached copy.
// Snapshot of the real chart (2026-09-01); ids may be missing for newer titles.
const FALLBACK_UPDATED_AT = "2026-09-01T00:00:00.000Z";
const FALLBACK_SOURCE = "seed: chart snapshot 2026-09-01 (facts only; ids partial)";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/stats") {
      await recordHit(env, "/stats");
      const stats = await readStats(env);
      return json({
        total_requests: stats.total,
        by_path: stats.by_path,
        d1_connected: !!env.DB,
        updatedAt: new Date().toISOString(),
      });
    }

    if (url.pathname === "/top250") {
      await recordHit(env, "/top250");
      const limit = Math.min(
        Math.max(parseInt(url.searchParams.get("limit") || "250", 10) || 250, 1),
        250
      );
      try {
        const result = await getTop250(env, false);
        return json({ ...result, data: result.data.slice(0, limit) });
      } catch (err) {
        return json({ error: "Failed to fetch chart", detail: String((err && err.message) || err) }, 503);
      }
    }

    if (url.pathname === "/refresh") {
      await recordHit(env, "/refresh");
      if (env.ADMIN_KEY) {
        const key = url.searchParams.get("key") || "";
        if (key !== env.ADMIN_KEY) return json({ error: "Unauthorized" }, 401);
      }
      try {
        const result = await getTop250(env, true);
        if (result.fallback) {
          return json(
            {
              error: "Refresh failed",
              detail: result.liveError || "Live fetch failed",
              fallback: true,
              note: result.note,
              count: result.data.length,
            },
            503
          );
        }
        return json({ refreshed: true, count: result.data.length, updatedAt: result.updatedAt });
      } catch (err) {
        return json({ error: "Refresh failed", detail: String((err && err.message) || err) }, 503);
      }
    }

    await recordHit(env, url.pathname === "/" ? "/" : "other");
    return new Response(helpHtml(), {
      headers: { "content-type": "text/html; charset=utf-8" },
    });
  },
};

// Cached chart when fresh, otherwise fetch live and cache. Falls back to
// the last good copy, then to the bundled seed.
async function getTop250(env, force) {
  if (!force && env.CACHE) {
    const cached = await env.CACHE.get(CACHE_KEY, "json");
    if (cached && Date.now() - cached.fetchedAt < DAY * 1000) {
      return { ...cached, stale: false };
    }
  }

  try {
    const data = await fetchLiveChart(env);
    const payload = {
      source: "live: r.jina.ai proxy of imdb.com/chart/top",
      updatedAt: new Date().toISOString(),
      fetchedAt: Date.now(),
      count: data.length,
      stale: false,
      data,
    };
    if (env.CACHE) {
      await env.CACHE.put(CACHE_KEY, JSON.stringify(payload), {
        expirationTtl: DAY * 7,
      });
    }
    return payload;
  } catch (err) {
    const liveError = String((err && err.message) || err);
    if (env.CACHE) {
      const cached = await env.CACHE.get(CACHE_KEY, "json");
      if (cached) return { ...cached, stale: true };
    }
    return {
      source: FALLBACK_SOURCE,
      updatedAt: FALLBACK_UPDATED_AT,
      fetchedAt: Date.now(),
      count: FALLBACK_LIST.length,
      stale: true,
      fallback: true,
      liveError,
      note: "Live fetch failed. Serving bundled seed data.",
      data: FALLBACK_LIST,
    };
  }
}

// Live chart via Jina Reader (returns the page as markdown). A JINA_API_KEY
// secret raises the rate limit a lot. Throws when the result is unusable.
async function fetchLiveChart(env) {
  const headers = { "X-With-Images-Summary": "false" };
  const jinaKey = (env.JINA_API_KEY || "").trim();
  if (jinaKey) headers.Authorization = "Bearer " + jinaKey;
  const res = await fetch(JINA_URL, { headers });
  if (!res.ok) {
    const body = (await res.text().catch(() => "")).slice(0, 200);
    throw new Error("Chart proxy responded with HTTP " + res.status + (body ? " — " + body : ""));
  }
  const text = await res.text();
  if (
    !text ||
    /AwsWafIntegration|window\.gokuProps|challenge\.js|Just a moment|Verify you are human/i.test(text)
  ) {
    throw new Error("Chart proxy returned a bot-check page instead of the chart");
  }
  const live = parseJinaChart(text);
  if (live.length < 200) {
    throw new Error(
      "Only parsed " + live.length + " titles from live chart (need 200+, got " + text.length + " chars)"
    );
  }
  return enrichWithIds(live);
}

// Jina sends one of two layouts. Keyed requests come back rich:
//   "#1 #### [Title](/title/tt1234567/...) * 1994 ... 9.3(3.2M)"
// Keyless requests are plain:
//   "#1\nTitle\n1994...\n9.3\n (3.2M)"
function parseJinaChart(text) {
  const heads = [...text.matchAll(/#(\d{1,3})\s*####\s*\[([^\]]+)\]\(([^)]+)\)/g)];
  if (heads.length >= 200) {
    const entries = [];
    const seen = new Set();
    for (let i = 0; i < heads.length; i++) {
      const rank = parseInt(heads[i][1], 10);
      if (rank < 1 || rank > 250 || seen.has(rank)) continue;
      seen.add(rank);
      const end = i + 1 < heads.length ? heads[i + 1].index : heads[i].index + 800;
      const chunk = text.slice(heads[i].index, end);
      const idm = heads[i][3].match(/\/title\/(tt\d+)/);
      const ym = chunk.match(/\*\s*(\d{4})/);
      const rm = chunk.match(/(\d{1,2}\.\d)\s*\(([\d.,]+)\s*([MK])?\)/);
      entries.push({
        rank,
        title: heads[i][2].trim(),
        id: idm ? idm[1] : null,
        year: ym ? parseInt(ym[1], 10) : null,
        rating: rm ? parseFloat(rm[1]) : null,
        votes: rm ? parseCompactVotes(rm[2], rm[3]) : null,
      });
    }
    entries.sort((a, b) => a.rank - b.rank);
    if (entries.length >= 200) return entries;
  }

  const re = /#(\d{1,3})\s*\n+([^#\n]+?)\s*\n+(\d{4})/g;
  const marks = [];
  let m;
  while ((m = re.exec(text)) !== null) {
    const rank = parseInt(m[1], 10);
    if (rank < 1 || rank > 250) continue;
    marks.push({ rank, title: m[2].trim(), year: parseInt(m[3], 10), end: re.lastIndex, at: m.index });
  }
  const seen = new Set();
  const entries = [];
  for (const e of marks) {
    if (seen.has(e.rank)) continue;
    seen.add(e.rank);
    entries.push(e);
  }
  entries.sort((a, b) => a.rank - b.rank);
  for (let i = 0; i < entries.length; i++) {
    const from = entries[i].end;
    const to = i + 1 < entries.length ? entries[i + 1].at : from + 400;
    const rm = text.slice(from, to).match(/(\d{1,2}\.\d)\s*\n+\s*\(([\d.,]+)\s*([MK])?\)/);
    entries[i].rating = rm ? parseFloat(rm[1]) : null;
    entries[i].votes = rm ? parseCompactVotes(rm[2], rm[3]) : null;
    entries[i].id = null;
    delete entries[i].end;
    delete entries[i].at;
  }
  return entries;
}

// "3.2"+"M" -> 3200000, "850"+"K" -> 850000, plain numbers as-is.
function parseCompactVotes(num, suffix) {
  const n = parseFloat(String(num).replace(/,/g, ""));
  if (!isFinite(n)) return null;
  if (suffix === "M") return Math.round(n * 1e6);
  if (suffix === "K") return Math.round(n * 1e3);
  return Math.round(n);
}

// Fill gaps (missing ids/ratings/votes) from the bundled seed, matched by title.
function enrichWithIds(live) {
  const byTitle = new Map();
  for (const s of FALLBACK_LIST) {
    const k = normTitle(s.title);
    if (k && !byTitle.has(k)) byTitle.set(k, s);
  }
  return live.map((e) => {
    const seed = byTitle.get(normTitle(e.title));
    const id = e.id || (seed ? seed.id : null);
    return {
      rank: e.rank,
      id,
      title: e.title,
      year: e.year,
      rating: e.rating ?? (seed ? seed.rating : null),
      votes: e.votes ?? (seed ? seed.votes : null),
      url: id ? "https://www.imdb.com/title/" + id + "/" : null,
    };
  });
}

function normTitle(s) {
  return decodeEntities(s || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
}

// The seed data contains escaped entities like &apos;.
function decodeEntities(s) {
  return s
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;|&#0*39;|&#x0*27;/gi, "'")
    .replace(/&#0*(\d+);/g, (_, n) => String.fromCharCode(parseInt(n, 10)))
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCharCode(parseInt(h, 16)));
}

// Request counters in D1. Stats failures never break the API.
async function recordHit(env, path) {
  try {
    if (!env.DB) return;
    const known = path === "/" || path === "/top250" || path === "/refresh" || path === "/stats";
    const batch = [
      env.DB.prepare(
        'INSERT INTO hits("key", count) VALUES (\'total\', 1) ON CONFLICT("key") DO UPDATE SET count = count + 1'
      ),
      env.DB.prepare(
        'INSERT INTO hits("key", count) VALUES (?1, 1) ON CONFLICT("key") DO UPDATE SET count = count + 1'
      ).bind(known ? path : "other"),
    ];
    await env.DB.batch(batch);
  } catch {
    // ignore
  }
}

async function readStats(env) {
  const out = { total: 0, by_path: {} };
  try {
    if (!env.DB) return out;
    const rows = await env.DB.prepare('SELECT "key", count FROM hits').all();
    for (const r of rows.results || []) {
      if (r.key === "total") out.total = r.count;
      else out.by_path[r.key] = r.count;
    }
  } catch {
    // ignore, return zeros
  }
  return out;
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
</ul>
</body></html>`;
}
