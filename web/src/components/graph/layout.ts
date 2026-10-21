import { graphTokens, shillers, type Outcome } from "@/lib/demo";

/**
 * Deterministic layout of the shiller → token graph. Pure function of `demo.ts`: a seeded PRNG plus a short
 * force simulation that runs once, so every load (and the server) gets exactly the same picture.
 * Positions are unit coordinates in [-1, 1]; the engine stretches them to the canvas.
 */

export function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export type CloudPoint = {
  /** offset from the shiller, in "radius units" (1 = half of the shorter canvas side) */
  dx: number;
  dy: number;
  outcome: Outcome;
  size: number;
  alpha: number;
  phase: number;
  /** 0..1 position in the assembly timeline */
  delay: number;
  /** scatter start offset, px */
  sx: number;
  sy: number;
};

export type ShillerNode = {
  id: string;
  handle: string;
  smarts: number;
  ca: number;
  x: number;
  y: number;
  /** core radius, px */
  r: number;
  sx: number;
  sy: number;
  cloud: CloudPoint[];
  /** indices into `tokens` */
  tokens: number[];
};

export type TokenNode = {
  id: string;
  ticker: string;
  outcome: Outcome;
  x: number;
  y: number;
  r: number;
  sx: number;
  sy: number;
  /** indices into `shillers` */
  shillers: number[];
};

export type Edge = { s: number; t: number; bend: number };

export type GraphLayout = {
  shillers: ShillerNode[];
  tokens: TokenNode[];
  edges: Edge[];
  cloudCount: number;
};

let cached: GraphLayout | null = null;

export function buildLayout(): GraphLayout {
  if (cached) return cached;
  const rnd = mulberry32(20261018);
  const gauss = () => {
    const u = Math.max(1e-9, rnd());
    const v = rnd();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  };

  const nS = shillers.length;
  const nT = graphTokens.length;
  const total = nS + nT;
  const px = new Float64Array(total);
  const py = new Float64Array(total);

  const sIndex = new Map(shillers.map((s, i) => [s.id, i]));
  const edges: Edge[] = [];
  const tokenShillers: number[][] = graphTokens.map((t) => t.shilledBy.map((id) => sIndex.get(id)!));
  tokenShillers.forEach((list, ti) =>
    list.forEach((si) => edges.push({ s: si, t: ti, bend: ((si * 7 + ti * 13) % 2 === 0 ? 1 : -1) * (0.1 + ((si + ti) % 3) * 0.05) })),
  );

  // initial positions: shillers on a ring, tokens at the centre of their shillers
  for (let i = 0; i < nS; i++) {
    const ang = (i / nS) * Math.PI * 2 - Math.PI / 2 + (rnd() - 0.5) * 0.5;
    const rad = 0.55 + rnd() * 0.2;
    px[i] = Math.cos(ang) * rad;
    py[i] = Math.sin(ang) * rad;
  }
  for (let t = 0; t < nT; t++) {
    const list = tokenShillers[t];
    let cx = 0;
    let cy = 0;
    for (const s of list) {
      cx += px[s];
      cy += py[s];
    }
    px[nS + t] = cx / list.length + (rnd() - 0.5) * 0.25;
    py[nS + t] = cy / list.length + (rnd() - 0.5) * 0.25;
  }

  const ITER = 420;
  const fx = new Float64Array(total);
  const fy = new Float64Array(total);
  for (let it = 0; it < ITER; it++) {
    const cool = 1 - it / ITER;
    const step = 0.012 + 0.05 * cool;
    fx.fill(0);
    fy.fill(0);
    for (let i = 0; i < total; i++) {
      for (let j = i + 1; j < total; j++) {
        const dx = px[j] - px[i];
        const dy = py[j] - py[i];
        const d2 = dx * dx + dy * dy + 1e-4;
        const d = Math.sqrt(d2);
        const bothS = i < nS && j < nS;
        const bothT = i >= nS && j >= nS;
        const k = bothS ? 0.09 : bothT ? 0.012 : 0.02;
        const f = Math.min(3, k / d2);
        const ux = dx / d;
        const uy = dy / d;
        fx[i] -= ux * f;
        fy[i] -= uy * f;
        fx[j] += ux * f;
        fy[j] += uy * f;
      }
    }
    for (const e of edges) {
      const a = e.s;
      const b = nS + e.t;
      const dx = px[b] - px[a];
      const dy = py[b] - py[a];
      const d = Math.sqrt(dx * dx + dy * dy) + 1e-6;
      const f = (d - 0.34) * 0.55;
      const ux = dx / d;
      const uy = dy / d;
      fx[a] += ux * f;
      fy[a] += uy * f;
      fx[b] -= ux * f;
      fy[b] -= uy * f;
    }
    for (let i = 0; i < total; i++) {
      const g = i < nS ? 0.3 : 0.1;
      fx[i] -= px[i] * g;
      fy[i] -= py[i] * g;
      px[i] += Math.max(-0.05, Math.min(0.05, fx[i] * step));
      py[i] += Math.max(-0.05, Math.min(0.05, fy[i] * step));
    }
  }

  // normalise to fill [-0.92, 0.92] on both axes
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < total; i++) {
    minX = Math.min(minX, px[i]);
    maxX = Math.max(maxX, px[i]);
    minY = Math.min(minY, py[i]);
    maxY = Math.max(maxY, py[i]);
  }
  const nx = (v: number) => ((v - minX) / (maxX - minX || 1)) * 1.84 - 0.92;
  const ny = (v: number) => ((v - minY) / (maxY - minY || 1)) * 1.84 - 0.92;

  const scatter = () => {
    const ang = rnd() * Math.PI * 2;
    const mag = 140 + rnd() * 260;
    return [Math.cos(ang) * mag, Math.sin(ang) * mag] as const;
  };

  let cloudCount = 0;
  const sNodes: ShillerNode[] = shillers.map((s, i) => {
    const logF = Math.log10(s.followers);
    const r = 6.6 + ((Math.min(5, Math.max(3.7, logF)) - 3.7) / 1.3) * 3.4;
    const count = Math.round(16 + s.calls * 0.5);
    const sigma = 0.1 + (s.calls / 112) * 0.06;
    const cloud: CloudPoint[] = [];
    for (let k = 0; k < count; k++) {
      let dx = gauss() * sigma;
      let dy = gauss() * sigma;
      const rad = Math.hypot(dx, dy);
      const max = sigma * 2.3;
      if (rad > max) {
        dx *= max / rad;
        dy *= max / rad;
      }
      const u = rnd();
      const outcome: Outcome =
        u < s.hitRate ? "success" : u < s.hitRate + s.rugRate ? "rug" : rnd() < 0.62 ? "dead" : "neutral";
      const [sx, sy] = scatter();
      cloud.push({
        dx,
        dy,
        outcome,
        size: 0.8 + rnd() * 1.1,
        alpha: 0.28 + rnd() * 0.3,
        phase: rnd() * Math.PI * 2,
        delay: rnd(),
        sx,
        sy,
      });
    }
    cloudCount += count;
    const [sx, sy] = scatter();
    return { id: s.id, handle: s.handle, smarts: s.smarts, ca: s.ca, x: nx(px[i]), y: ny(py[i]), r, sx, sy, cloud, tokens: [] };
  });

  const tNodes: TokenNode[] = graphTokens.map((t, ti) => {
    const [sx, sy] = scatter();
    return {
      id: t.id,
      ticker: t.ticker,
      outcome: t.outcome,
      x: nx(px[nS + ti]),
      y: ny(py[nS + ti]),
      r: 3.4 + tokenShillers[ti].length * 1.1,
      sx,
      sy,
      shillers: tokenShillers[ti],
    };
  });
  tNodes.forEach((t, ti) => t.shillers.forEach((si) => sNodes[si].tokens.push(ti)));

  cached = { shillers: sNodes, tokens: tNodes, edges, cloudCount };
  return cached;
}
