import type { CallChartProps, MarketPoint } from "@/components/chart/types";
import { demoSignal, shillerById } from "./demo";

const start = Date.UTC(2026, 9, 18, 12) / 1000;
let seed = 719;
const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
const anchors = [[0, 28], [3, 40], [5, 60], [7, demoSignal.marketCapSol], [12, 150], [18, 285], [25, 438], [32, 335], [39, 372], [48, 280], [55, 268], [60, 250]];
const points: MarketPoint[] = Array.from({ length: 121 }, (_, i) => {
  const minute = i / 2;
  const next = anchors.findIndex(([t]) => t >= minute);
  const [t1, v1] = anchors[Math.max(0, next - 1)];
  const [t2, v2] = anchors[next];
  const fraction = t2 === t1 ? 0 : (minute - t1) / (t2 - t1);
  const noise = Math.sin(fraction * Math.PI) * (random() - 0.5) * (minute < 7 ? 5 : 28);
  return { t: start + i * 30, mcapSol: Math.round((v1 + (v2 - v1) * fraction + noise) * 100) / 100 };
});

/** Fictional calls omit href, so CallChart uses generated avatars without network requests. */
export const demoChart: Pick<CallChartProps, "points" | "volume" | "calls" | "marker"> = {
  points,
  volume: Array.from({ length: 61 }, (_, minute) => {
    const change = points[minute * 2].mcapSol - points[Math.max(0, minute * 2 - 2)].mcapSol;
    const total = 1.2 + Math.abs(change) * 0.8 + random() * 6;
    const buyShare = change >= 0 ? 0.64 + random() * 0.2 : 0.18 + random() * 0.19;
    return { t: start + minute * 60, buySol: total * buyShare, sellSol: total * (1 - buyShare) };
  }),
  calls: [...demoSignal.inside, { shillerId: "s3", minute: 12 }, { shillerId: "s5", minute: 18 }].map(({ shillerId, minute }) => {
    const shiller = shillerById(shillerId);
    return { t: start + minute * 60, username: shiller.handle.slice(1), followers: shiller.followers, tone: shiller.rugRate > 0.3 ? "bad" : "good" };
  }),
  marker: { t: start + demoSignal.ageMinutes * 60, label: "Kairos signal" },
};
