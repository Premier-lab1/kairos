import "server-only";
import { query } from "@/lib/server/db";

export type KolTrade = {
  signature: string; wallet_id: string; kol_id: string; username: string; avatar_url: string | null;
  smart_followers_count: number | null; ca: string; mint: string;
  token_id: string | null; symbol: string | null; ts: Date | string;
  side: "buy" | "sell"; sol_amount: string; market_cap_sol: string | null;
};

const feedSql = `SELECT kt.signature, kt.wallet_id::text, k.id::text AS kol_id, k.username, k.avatar_url,
  k.smart_followers_count, coalesce(c.ca,0)::text AS ca, kt.mint,
  coalesce(kt.token_id,t.id)::text AS token_id, t.symbol, kt.ts, kt.side, kt.sol_amount, kt.market_cap_sol
  FROM kol_trades kt JOIN kols k ON k.id = coalesce($2::bigint, kt.kol_id)
  LEFT JOIN tokens t ON t.chain='SOLANA' AND t.mint=kt.mint
  LEFT JOIN LATERAL (SELECT count(DISTINCT ca) AS ca FROM mentions WHERE kol_id=k.id) c ON true
  WHERE ($1::text IS NULL OR kt.side=$1)
    AND ($2::bigint IS NULL OR EXISTS (SELECT 1 FROM kol_wallets kw WHERE kw.wallet_id=kt.wallet_id AND kw.kol_id=$2))
  ORDER BY kt.ts DESC, kt.signature, kt.wallet_id, kt.mint LIMIT $3`;

export async function getKolTrades(side: "buy" | "sell" | null = null, kolId: string | null = null, limit = 200) {
  return query<KolTrade>(feedSql, [side, kolId, Math.min(200, Math.max(1, limit))]);
}

export function getShillerTrades(id: string) {
  return getKolTrades(null, id, 20);
}

export type KolWalletSummary = {
  id: string; username: string; avatar_url: string | null; smart_followers_count: number | null; ca: string;
  buys: string; sells: string; net: string; tokens: string; wallets: string;
};

export async function getKolWalletSummaries() {
  return query<KolWalletSummary>(`SELECT k.id::text, k.username, k.avatar_url, k.smart_followers_count,
    coalesce(c.ca,0)::text AS ca, w.wallets::text,
    coalesce(s.buys,0)::text AS buys, coalesce(s.sells,0)::text AS sells,
    (coalesce(s.buys,0)-coalesce(s.sells,0))::text AS net, coalesce(s.tokens,0)::text AS tokens
    FROM kols k
    JOIN LATERAL (SELECT count(DISTINCT wallet_id) AS wallets FROM kol_wallets WHERE kol_id=k.id) w ON w.wallets>0
    LEFT JOIN LATERAL (SELECT count(DISTINCT ca) AS ca FROM mentions WHERE kol_id=k.id) c ON true
    LEFT JOIN LATERAL (SELECT sum(kt.sol_amount) FILTER (WHERE kt.side='buy') AS buys,
      sum(kt.sol_amount) FILTER (WHERE kt.side='sell') AS sells, count(DISTINCT kt.mint) AS tokens
      FROM kol_trades kt WHERE kt.ts >= now()-interval '24 hours'
        AND EXISTS (SELECT 1 FROM kol_wallets kw WHERE kw.wallet_id=kt.wallet_id AND kw.kol_id=k.id)) s ON true
    ORDER BY (coalesce(s.buys,0)-coalesce(s.sells,0)) DESC, c.ca DESC NULLS LAST, k.id LIMIT 200`);
}
