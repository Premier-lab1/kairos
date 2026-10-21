/**
 * Demo data for the landing page. Everything here is FICTIONAL: handles, tickers and numbers
 * are made up for illustration. Every block that renders it must show <DemoTag />.
 * Outcome labels follow docs/outcomes.md (rules v1), measured from the first shill.
 */

export type Outcome = "success" | "rug" | "dead" | "neutral" | "pending";

export const outcomeLabel: Record<Outcome, string> = {
  success: "win",
  rug: "rug",
  dead: "dead",
  neutral: "neutral",
  pending: "pending",
};

/** CSS colours for outcomes (tokens from globals.css). */
export const outcomeColor: Record<Outcome, string> = {
  success: "var(--color-win)",
  rug: "var(--color-rug)",
  dead: "var(--color-flat)",
  neutral: "#a3a39e",
  pending: "var(--color-accent)",
};

export type Shiller = {
  id: string;
  handle: string;
  followers: number;
  /** Fictional smart-follower count, in the style of Frontrun smart followers. */
  smarts: number;
  /** Distinct memecoin contract addresses called across this shiller's history. */
  ca: number;
  /** tokens with a labelled 24h outcome after this shiller's first mention */
  calls: number;
  /** share of `success` among labelled calls, 0..1 */
  hitRate: number;
  /** share of `rug` among labelled calls, 0..1 */
  rugRate: number;
  /** median minute after token creation when this shiller posts the CA */
  medianMinute: number;
};

export type CaTier = "low" | "mid" | "high";

/** Client rule: 0–9 green, 10–19 yellow, 20 and above red (10 is yellow, 20 is red). */
export function caTier(ca: number): CaTier {
  return ca < 10 ? "low" : ca < 20 ? "mid" : "high";
}

export const caTierColor: Record<CaTier, string> = {
  low: "var(--color-win)",
  mid: "var(--color-warn)",
  high: "var(--color-rug)",
};

export const shillers: Shiller[] = [
  { id: "s1", handle: "@moth_caller", followers: 48200, smarts: 450, ca: 22, calls: 64, hitRate: 0.41, rugRate: 0.09, medianMinute: 6 },
  { id: "s2", handle: "@deep_trench", followers: 21900, smarts: 120, ca: 6, calls: 37, hitRate: 0.35, rugRate: 0.14, medianMinute: 9 },
  { id: "s3", handle: "@curve_witch", followers: 93400, smarts: 38, ca: 41, calls: 112, hitRate: 0.22, rugRate: 0.31, medianMinute: 4 },
  { id: "s4", handle: "@solnomad_x", followers: 12800, smarts: 205, ca: 10, calls: 29, hitRate: 0.38, rugRate: 0.07, medianMinute: 11 },
  { id: "s5", handle: "@pumpstrat", followers: 67100, smarts: 76, ca: 35, calls: 88, hitRate: 0.17, rugRate: 0.42, medianMinute: 2 },
  { id: "s6", handle: "@quiet_wallet", followers: 5400, smarts: 92, ca: 8, calls: 18, hitRate: 0.44, rugRate: 0.06, medianMinute: 14 },
  { id: "s7", handle: "@ape_ledger", followers: 33700, smarts: 184, ca: 20, calls: 51, hitRate: 0.29, rugRate: 0.2, medianMinute: 7 },
];

export type GraphToken = {
  id: string;
  ticker: string;
  outcome: Outcome;
  /** shiller ids who posted this CA */
  shilledBy: string[];
};

/** Past tokens for the shiller → token graph. */
export const graphTokens: GraphToken[] = [
  { id: "t1", ticker: "$LANTERN", outcome: "success", shilledBy: ["s1", "s4", "s6"] },
  { id: "t2", ticker: "$GLOOP", outcome: "rug", shilledBy: ["s3", "s5"] },
  { id: "t3", ticker: "$TIDEPOOL", outcome: "success", shilledBy: ["s1", "s2"] },
  { id: "t4", ticker: "$WICKER", outcome: "dead", shilledBy: ["s5", "s7"] },
  { id: "t5", ticker: "$NIMBUSK", outcome: "success", shilledBy: ["s2", "s4", "s6"] },
  { id: "t6", ticker: "$FROGLET", outcome: "rug", shilledBy: ["s3", "s5", "s7"] },
  { id: "t7", ticker: "$EMBERLY", outcome: "neutral", shilledBy: ["s1", "s7"] },
  { id: "t8", ticker: "$PEBBLO", outcome: "dead", shilledBy: ["s3"] },
  { id: "t9", ticker: "$ORBITA", outcome: "success", shilledBy: ["s6", "s1"] },
  { id: "t10", ticker: "$CRUMBO", outcome: "rug", shilledBy: ["s5"] },
  { id: "t11", ticker: "$VELVETO", outcome: "neutral", shilledBy: ["s2", "s7"] },
  { id: "t12", ticker: "$MIRELL", outcome: "success", shilledBy: ["s4", "s2"] },
  { id: "t13", ticker: "$SNORKA", outcome: "dead", shilledBy: ["s3", "s7"] },
  { id: "t14", ticker: "$QUILLY", outcome: "rug", shilledBy: ["s5", "s3"] },
  { id: "t15", ticker: "$DRIFTO", outcome: "success", shilledBy: ["s1", "s6", "s4"] },
  { id: "t16", ticker: "$BRAMBL", outcome: "neutral", shilledBy: ["s7"] },
];

/** The live (demo) token the signal card, hero and bot message are about. */
export const demoSignal = {
  ticker: "$HALCYON",
  ca: "7xKp…h4Qm",
  ageMinutes: 7,
  marketCapSol: 84,
  holders: 212,
  /** confidence 0..100 from rules + stats, not from the LLM */
  confidence: 72,
  verdict: "Worth a look",
  inside: [
    { shillerId: "s6", minute: 3 },
    { shillerId: "s1", minute: 5 },
    { shillerId: "s4", minute: 7 },
  ],
  /** past cases with the most similar picture (who, when, how many) */
  similar: [
    { tokenId: "t15", similarity: 0.86 },
    { tokenId: "t1", similarity: 0.81 },
    { tokenId: "t9", similarity: 0.74 },
  ],
  risks: ["Creator launched 4 tokens this week, 2 of them rugged", "Liquidity is still on the Pump.fun bonding curve"],
} as const;

export const shillerById = (id: string) => shillers.find((s) => s.id === id)!;
export const tokenById = (id: string) => graphTokens.find((t) => t.id === id)!;
