/**
 * Shared composition of the hero scene ("cluster signal").
 * Used by BOTH the server-rendered SVG still (HeroStill) and the three.js scene (scene.ts),
 * so the canvas can fade in over the still without a visible jump.
 * No three.js imports here — this file runs on the server.
 */

import { demoSignal, shillerById } from "@/lib/demo";

export type Vec3 = readonly [number, number, number];

/** Camera: on +z axis looking at the origin, vertical fov in degrees. */
export const CAMERA = { z: 14, fov: 38 } as const;

export type Cluster = {
  id: string;
  center: Vec3;
  /** ellipsoid radii (1 sigma-ish) */
  radii: Vec3;
  /** muted tint — orange is reserved for the token/signal */
  color: string;
  count: number;
  countSmall: number;
};

export const CLUSTERS: Cluster[] = [
  { id: "A", center: [-2.7, 1.5, -0.8], radii: [1.35, 0.85, 0.7], color: "#aab6c8", count: 2200, countSmall: 900 },
  { id: "B", center: [2.6, 1.9, -1.6], radii: [1.4, 0.8, 0.8], color: "#d2c4ae", count: 2200, countSmall: 900 },
  { id: "C", center: [-1.9, -2.2, 0.3], radii: [1.15, 0.8, 0.6], color: "#8fb09c", count: 1800, countSmall: 750 },
  { id: "D", center: [3.0, -1.5, -0.6], radii: [1.25, 0.9, 0.7], color: "#9c9c97", count: 1800, countSmall: 750 },
  { id: "E", center: [0.5, 3.2, -3.4], radii: [1.7, 0.7, 0.9], color: "#b08a88", count: 1600, countSmall: 650 },
];

/** Sparse background dust over the whole volume. */
export const DUST = { count: 1500, countSmall: 600, color: "#8a8a85", box: [6.5, 4.8, -7, 2] as const };

export type HeroNode = {
  shillerId: string;
  handle: string;
  pos: Vec3;
  /** minute after token creation when this shiller posted the CA (insiders only) */
  minute?: number;
  /** insiders light up and draw an arc to the token; the rest are dim context nodes */
  inside: boolean;
};

const insideById = new Map<string, number>(demoSignal.inside.map((i) => [i.shillerId, i.minute]));

const nodePositions: Record<string, Vec3> = {
  s6: [-2.15, 1.05, -0.2], // cluster A
  s1: [1.95, 1.35, -0.9], // cluster B
  s4: [-1.35, -1.6, 0.5], // cluster C
  s3: [0.9, 3.05, -3.0], // cluster E (rug-ish tint)
  s5: [3.25, -1.1, -0.4], // cluster D
  s7: [2.6, -2.05, -0.9], // cluster D
};

export const NODES: HeroNode[] = Object.entries(nodePositions).map(([id, pos]) => ({
  shillerId: id,
  handle: shillerById(id).handle,
  pos,
  minute: insideById.get(id),
  inside: insideById.has(id),
}));

/** Insiders in the order they appear (minute 3 → 5 → 7). */
export const INSIDERS = NODES.filter((n) => n.inside).sort((a, b) => (a.minute ?? 0) - (b.minute ?? 0));

export const TOKEN: Vec3 = [0, 0, 0];
export const RING_RADIUS = 1.25;
export const CONFIDENCE = demoSignal.confidence / 100;

/**
 * Framing presets. dx/dy = shift of the composition as a fraction of the viewport width/height,
 * s = zoom. Applied as camera.zoom + setViewOffset in 3D and as a CSS transform on the SVG still,
 * which are mathematically the same 2D transform of the rendered image.
 */
export const PRESETS = {
  mobile: { dx: 0, dy: 0, s: 0.5 },
  desktop: { dx: 0.2, dy: -0.02, s: 1 },
} as const;
export const DESKTOP_QUERY = "(min-width: 768px)";

/** Control point of the arc from a node to the token: bows sideways and toward the camera. */
export function arcControl(a: Vec3, b: Vec3): Vec3 {
  const mx = (a[0] + b[0]) / 2;
  const my = (a[1] + b[1]) / 2;
  const mz = (a[2] + b[2]) / 2;
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len = Math.hypot(dx, dy) || 1;
  // perpendicular in XY, consistent handedness so arcs swirl the same way
  const px = -dy / len;
  const py = dx / len;
  const bow = 0.42 * len;
  return [mx + px * bow, my + py * bow, mz + 0.9];
}

/**
 * Perspective projection to "vertical NDC" units: y ∈ [-1, 1] spans the viewport height,
 * x uses the same unit (so it is aspect-independent). Matches CAMERA.
 */
export function project(p: Vec3): [number, number] {
  const k = Math.tan((CAMERA.fov * Math.PI) / 360);
  const d = CAMERA.z - p[2];
  return [p[0] / (d * k), p[1] / (d * k)];
}

/** Small deterministic PRNG so the server still and the client scene are reproducible. */
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

/** Approximate gaussian in [-3, 3] from a uniform source (Box–Muller). */
export function gauss(rng: () => number) {
  const u = 1 - rng();
  const v = rng();
  const g = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  return Math.max(-3, Math.min(3, g));
}

/** One point inside a cluster: dense core, soft ellipsoidal falloff. */
export function clusterPoint(rng: () => number, c: Cluster): [number, number, number] {
  return [
    c.center[0] + gauss(rng) * c.radii[0] * 0.5,
    c.center[1] + gauss(rng) * c.radii[1] * 0.5,
    c.center[2] + gauss(rng) * c.radii[2] * 0.5,
  ];
}

export function dustPoint(rng: () => number): [number, number, number] {
  const [bx, by, z0, z1] = DUST.box;
  return [(rng() * 2 - 1) * bx, (rng() * 2 - 1) * by, z0 + rng() * (z1 - z0)];
}

export const SCENE_LABEL =
  "Point clouds are shiller communities and their past mentions; in the centre is a new token " +
  `${demoSignal.ticker}, with lines from the shillers already in: ` +
  INSIDERS.map((n) => `${n.handle} at minute ${n.minute}`).join(", ") +
  `. Confidence ${demoSignal.confidence} out of 100. Demo data.`;
