import { checkAuth } from '../middleware/auth.js';
import { loadSiteSettings } from '../utils/settings.js';

const MAX_BODY = 32768;
const WINDOW = 120;
export const STALE_AFTER = 180;
const encoder = new TextEncoder();
const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
});

export async function verifyReport(secret, timestamp, signature, body, now = Math.floor(Date.now() / 1000)) {
  if (!secret || secret.length < 32 || !/^\d{10}$/.test(timestamp || '') || Math.abs(now - Number(timestamp)) > WINDOW || !/^[a-f0-9]{64}$/.test(signature || '')) return false;
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']);
  return crypto.subtle.verify('HMAC', key, Uint8Array.from(signature.match(/../g), x => parseInt(x, 16)), encoder.encode(timestamp + '\n' + body));
}

export function validateSample(sample, now) {
  if (!sample || !/^[a-z0-9_-]{1,40}$/.test(sample.id || '') || !Number.isInteger(sample.sampled_at) || Math.abs(now - sample.sampled_at) > WINDOW) throw new Error('Invalid sample');
  const m = sample.metrics;
  if (!m || Array.isArray(m) || typeof m !== 'object' || !['ok', 'warn', 'down', 'unknown'].includes(m.status)) throw new Error('Invalid metrics');
  const clean = { status: m.status };
  for (const [k, v] of Object.entries(m)) {
    if (!/^[a-z][a-z0-9_]{0,47}$/.test(k)) continue;
    if (typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= 1e15) clean[k] = v;
    if (['online_source','peak_source','peak_day','core_services','version'].includes(k) && typeof v === 'string') clean[k] = v.slice(0, 100);
  }
  if (m.collection_error === true) {
    clean.collection_error = true;
    clean.status = 'unknown';
    clean.online = -1;
  }
  if (clean.online !== undefined && (!Number.isInteger(clean.online) || clean.online < -1)) throw new Error('Invalid online count');
  return { id: sample.id, sampled_at: sample.sampled_at, metrics: clean };
}

export function presentGame(row, now) {
  let metrics;
  try { metrics = JSON.parse(row.metrics_json); } catch { metrics = {}; }
  const stale = !row.sampled_at || now - row.sampled_at > STALE_AFTER;
  return { id: row.id, name: row.name, server_id: row.server_id, server_name: row.server_name,
    online_source: row.online_source, sampled_at: row.sampled_at, received_at: row.received_at,
    stale, metrics, status: stale || metrics.collection_error ? 'unknown' : metrics.status || 'unknown',
    online: stale || metrics.collection_error || metrics.online < 0 ? null : metrics.online ?? null };
}

export async function handleGameReport(request, env) {
  const declared = Number(request.headers.get('Content-Length'));
  if (declared > MAX_BODY) return json({ error: 'Payload too large' }, 413);
  // Stream cap also protects chunked requests before JSON decoding.
  const reader = request.body?.getReader();
  if (!reader) return json({ error: 'Missing body' }, 400);
  const chunks = []; let size = 0;
  for (;;) {
    const { value, done } = await reader.read(); if (done) break;
    size += value.length;
    if (size > MAX_BODY) { await reader.cancel(); return json({ error: 'Payload too large' }, 413); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const c of chunks) { bytes.set(c, offset); offset += c.length; }
  const body = new TextDecoder().decode(bytes);
  const now = Math.floor(Date.now() / 1000);
  if (!await verifyReport(env.GAME_REPORT_SECRET, request.headers.get('X-Game-Timestamp'), request.headers.get('X-Game-Signature'), body, now)) return json({ error: 'Unauthorized' }, 401);
  let samples;
  try {
    const payload = JSON.parse(body);
    if (!Array.isArray(payload.games) || !payload.games.length || payload.games.length > 20) throw new Error();
    samples = payload.games.map(s => validateSample(s, now));
    if (new Set(samples.map(s => s.id)).size !== samples.length) throw new Error();
  } catch { return json({ error: 'Invalid report' }, 400); }
  const registry = await env.DB.prepare('SELECT id FROM game_services').all();
  const allowed = new Set(registry.results.map(x => x.id));
  if (samples.some(s => !allowed.has(s.id))) return json({ error: 'Unknown game' }, 403);
  const statements = [];
  for (const s of samples) {
    // An old retry cannot replace a newer sample or insert a contradictory history point.
    statements.push(env.DB.prepare('INSERT OR IGNORE INTO game_history(game_id,sampled_at,online,status) SELECT id,?,?,? FROM game_services WHERE id=? AND sampled_at < ?').bind(s.sampled_at, s.metrics.online >= 0 ? s.metrics.online : null, s.metrics.status, s.id, s.sampled_at));
    statements.push(env.DB.prepare('UPDATE game_services SET metrics_json=?,sampled_at=?,received_at=? WHERE id=? AND sampled_at < ?').bind(JSON.stringify(s.metrics), s.sampled_at, now, s.id, s.sampled_at));
  }
  await env.DB.batch(statements);
  return json({ success: true });
}

export async function handleGames(request, env) {
  const sys = await loadSiteSettings(env.DB);
  if (!await checkAuth(request, env, sys)) return json({ error: 'Unauthorized' }, 401);
  const url = new URL(request.url);
  const game = url.searchParams.get('id');
  const now = Math.floor(Date.now() / 1000);
  if (game) {
    const hours = Number(url.searchParams.get('hours') || 6);
    if (!/^[a-z0-9_-]{1,40}$/.test(game) || ![1,6,24,168].includes(hours)) return json({ error: 'Invalid query' }, 400);
    // Five-minute buckets bound response size and D1 rows for 7-day views.
    const bucket = hours > 24 ? 300 : 60;
    const rows = await env.DB.prepare('SELECT CAST(sampled_at / ? AS INTEGER) * ? AS ts, MAX(online) AS online, MAX(CASE WHEN status != \'ok\' THEN 1 ELSE 0 END) AS issue FROM game_history WHERE game_id=? AND sampled_at>=? GROUP BY CAST(sampled_at / ? AS INTEGER) ORDER BY ts').bind(bucket,bucket,game,now-hours*3600,bucket).all();
    return json({ history: rows.results });
  }
  const rows = await env.DB.prepare('SELECT g.*,s.name AS server_name FROM game_services g LEFT JOIN servers s ON g.server_id=s.id ORDER BY g.id').all();
  return json({ games: rows.results.map(r => presentGame(r, now)), now });
}

export async function cleanupGameHistory(db) {
  await db.prepare('DELETE FROM game_history WHERE sampled_at < ?').bind(Math.floor(Date.now()/1000)-7*86400).run();
}
