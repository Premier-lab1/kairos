import "server-only";
import { Pool, type QueryResultRow } from "pg";

/**
 * Read-only connection to the Kairos database for the dashboard.
 * On the server DATABASE_URL_RO points at the `kairos_ro` role (SELECT only);
 * locally at the backtest copy `kairos_bt` (see web/README.md).
 */
const globalForPool = globalThis as unknown as { kairosPool?: Pool };

function pool(): Pool {
  if (!globalForPool.kairosPool) {
    const connectionString = process.env.DATABASE_URL_RO;
    if (!connectionString) throw new Error("DATABASE_URL_RO is not set");
    globalForPool.kairosPool = new Pool({ connectionString, max: 5, idleTimeoutMillis: 30_000, statement_timeout: 15_000 });
  }
  return globalForPool.kairosPool;
}

/**
 * The dashboard is public (21.10.2026): identical SELECTs within TTL_MS share one result, and concurrent identical
 * ones share one database round trip, so repeated visits cannot pile heavy aggregates onto the database.
 * Bounded by MAX_ENTRIES (oldest evicted first); failed queries are not cached.
 */
const TTL_MS = 30_000;
const MAX_ENTRIES = 500;
const globalForCache = globalThis as unknown as { kairosQueryCache?: Map<string, { at: number; rows: Promise<unknown[]> }> };
const cache = (globalForCache.kairosQueryCache ??= new Map());

/** Run a parameterised SELECT. Never interpolate user input into `sql` — pass it in `params`. */
export async function query<T extends QueryResultRow>(sql: string, params: unknown[] = [], ttlMs = TTL_MS): Promise<T[]> {
  const key = JSON.stringify([sql, params]);
  const now = Date.now();
  const hit = cache.get(key);
  if (hit && now - hit.at < ttlMs) return hit.rows as Promise<T[]>;
  const rows = pool().query<T>(sql, params).then(res => res.rows);
  cache.delete(key);
  cache.set(key, { at: now, rows });
  rows.catch(() => { if (cache.get(key)?.rows === rows) cache.delete(key); });
  while (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value!);
  return rows;
}
