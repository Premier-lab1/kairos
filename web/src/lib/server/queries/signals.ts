import "server-only";
import { query } from "@/lib/server/db";

export const numberOrNull = (v: unknown): number | null => v == null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v);
const num = (v: unknown) => numberOrNull(v) ?? 0;
const obj = (v: unknown): Record<string, unknown> => v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};
const list = (v: unknown): unknown[] => Array.isArray(v) ? v : [];
export function parseFeatures(value: unknown) {
  const f = obj(value), base = obj(f.base);
  return {
    inside: list(f.inside).map(value => { const s = obj(value); return { kolId: num(s.kol_id), username: String(s.username ?? "unknown"), minute: numberOrNull(s.minute), nFinal: num(s.n_final), success: num(s.success), rug: num(s.rug), lift: numberOrNull(s.lift) }; }),
    similar: list(f.similar).map(value => { const s = obj(value); return { tokenId: num(s.token_id), symbol: String(s.symbol ?? "Unknown"), similarity: num(s.similarity), label: String(s.label ?? "no_data"), peak: numberOrNull(s.peak_multiple) }; }),
    components: Object.fromEntries(Object.entries(obj(f.components)).map(([k, v]) => [k, num(v)])),
    risks: list(f.risks).filter((r): r is string => typeof r === "string"),
    base: { successRate: numberOrNull(base.success_rate), rugRate: numberOrNull(base.rug_rate), nFinal: numberOrNull(base.n_final), defaultUsed: base.default_used === true },
  };
}
export type Signal = { id: number; tokenId: number; symbol: string | null; mint: string; score: number; confidence: string | null; nShillers: number; firstMentionAt: string | null; updatedAt: string; features: ReturnType<typeof parseFeatures> };
type SignalRow = { id: string; token_id: string; symbol: string | null; mint: string; score: string; confidence: string | null; n_shillers: number; first_mention_at: Date | null; updated_at: Date; features: unknown };
export async function getSignals({ min = 0, confidence = null, limit = 100, tokenId = null }: { min?: number; confidence?: string | null; limit?: number; tokenId?: number | null } = {}): Promise<Signal[]> {
  // The local backtest copy predates migration 0005. JSON field access keeps
  // that read-only copy usable while preferring the live schema's columns.
  const rows = await query<SignalRow>(`SELECT s.id, s.token_id, t.symbol, t.mint, s.score, s.confidence,
    coalesce((to_jsonb(s)->>'n_shillers')::integer, (s.features->>'n_shillers')::integer, 0) n_shillers,
    coalesce((to_jsonb(s)->>'first_mention_at')::timestamptz, (s.features->'token'->>'first_mention_at')::timestamptz) first_mention_at,
    coalesce((to_jsonb(s)->>'updated_at')::timestamptz, s.created_at) updated_at, s.features
    FROM signals s JOIN tokens t ON t.id = s.token_id
    WHERE s.scorer_version = 's1' AND s.score >= $1 AND ($2::text IS NULL OR s.confidence = $2) AND ($3::bigint IS NULL OR s.token_id = $3)
    ORDER BY updated_at DESC, s.id DESC LIMIT $4`, [min, confidence, tokenId, Math.min(100, Math.max(1, limit))]);
  return rows.map(r => ({ id: Number(r.id), tokenId: Number(r.token_id), symbol: r.symbol, mint: r.mint, score: Number(r.score), confidence: r.confidence, nShillers: Number(r.n_shillers), firstMentionAt: r.first_mention_at?.toISOString() ?? null, updatedAt: r.updated_at.toISOString(), features: parseFeatures(r.features) }));
}
