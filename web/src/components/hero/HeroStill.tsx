import {
  CLUSTERS,
  CONFIDENCE,
  DUST,
  INSIDERS,
  NODES,
  RING_RADIUS,
  TOKEN,
  arcControl,
  clusterPoint,
  dustPoint,
  mulberry32,
  project,
  type Vec3,
} from "./layout";
import { demoSignal } from "@/lib/demo";

const f = (n: number) => n.toFixed(4).replace(/\.?0+$/, "") || "0";

/** Project to SVG space (y down), in "vertical NDC" units: the viewBox is -2..2 × -1..1. */
function P(p: Vec3) {
  const [x, y] = project(p);
  return [x, -y] as const;
}

/**
 * Static still of the hero scene — the final state of the animation (all three insiders
 * connected, confidence ring filled). Server-rendered: visible before any JS, kept for
 * reduced-motion users and when WebGL is unavailable. The canvas fades in over it.
 */
export function HeroStill() {
  const rng = mulberry32(20261018);

  const clusters = CLUSTERS.map((c, ci) => {
    const n = 85 + (ci % 2) * 20;
    const pts: string[] = [];
    for (let i = 0; i < n; i++) {
      const p = clusterPoint(rng, c);
      const [x, y] = P(p);
      const r = 0.0028 + rng() * 0.0045;
      pts.push(`<circle cx="${f(x)}" cy="${f(y)}" r="${f(r)}" opacity="${f(0.25 + rng() * 0.6)}"/>`);
    }
    const [cx, cy] = P(c.center);
    const d = 14 - c.center[2];
    const k = Math.tan((38 * Math.PI) / 360) * d;
    return { c, cx, cy, rx: c.radii[0] / k, ry: c.radii[1] / k, html: pts.join("") };
  });

  const dust: string[] = [];
  for (let i = 0; i < 160; i++) {
    const [x, y] = P(dustPoint(rng));
    dust.push(`<circle cx="${f(x)}" cy="${f(y)}" r="${f(0.002 + rng() * 0.003)}" opacity="${f(0.15 + rng() * 0.3)}"/>`);
  }

  const rays: string[] = [];
  for (let i = 0; i < 56; i++) {
    const a = (i / 56) * Math.PI * 2 + rng() * 0.08;
    const r0 = 0.22 + rng() * 0.1;
    const r1 = 0.5 + Math.pow(rng(), 2.2) * 0.9;
    const z = (rng() - 0.5) * 0.4;
    const [x0, y0] = P([Math.cos(a) * r0, Math.sin(a) * r0, z]);
    const [x1, y1] = P([Math.cos(a) * r1, Math.sin(a) * r1, z]);
    rays.push(`<line x1="${f(x0)}" y1="${f(y0)}" x2="${f(x1)}" y2="${f(y1)}" opacity="${f(0.25 + rng() * 0.5)}"/>`);
  }

  // confidence ring: from 12 o'clock, clockwise
  const ringPath = (() => {
    const N = 96;
    const parts: string[] = [];
    for (let i = 0; i <= N; i++) {
      const u = (i / N) * CONFIDENCE;
      const a = Math.PI / 2 - u * Math.PI * 2;
      const [x, y] = P([Math.cos(a) * RING_RADIUS, Math.sin(a) * RING_RADIUS, 0]);
      parts.push(`${i === 0 ? "M" : "L"}${f(x)} ${f(y)}`);
    }
    return parts.join("");
  })();
  const [tx, ty] = P(TOKEN);
  const ringR = project([RING_RADIUS, 0, 0])[0];

  return (
    <svg
      viewBox="-2 -1 4 2"
      preserveAspectRatio="xMidYMid slice"
      className="block h-full w-full"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        {clusters.map(({ c }) => (
          <radialGradient key={c.id} id={`hero-g-${c.id}`}>
            <stop offset="0" stopColor={c.color} stopOpacity="0.16" />
            <stop offset="0.55" stopColor={c.color} stopOpacity="0.05" />
            <stop offset="1" stopColor={c.color} stopOpacity="0" />
          </radialGradient>
        ))}
        <radialGradient id="hero-g-token">
          <stop offset="0" stopColor="#ff4f17" stopOpacity="0.7" />
          <stop offset="0.3" stopColor="#ff4f17" stopOpacity="0.22" />
          <stop offset="1" stopColor="#ff4f17" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="hero-g-node">
          <stop offset="0" stopColor="#ededeb" stopOpacity="0.5" />
          <stop offset="1" stopColor="#ededeb" stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* cluster haze */}
      {clusters.map(({ c, cx, cy, rx, ry }) => (
        <ellipse key={c.id} cx={f(cx)} cy={f(cy)} rx={f(rx * 1.1)} ry={f(ry * 1.1)} fill={`url(#hero-g-${c.id})`} />
      ))}
      {/* dust + cluster points */}
      <g fill={DUST.color} dangerouslySetInnerHTML={{ __html: dust.join("") }} />
      {clusters.map(({ c, html }) => (
        <g key={c.id} fill={c.color} dangerouslySetInnerHTML={{ __html: html }} />
      ))}

      {/* arcs insiders → token */}
      <g fill="none" stroke="#ff4f17" strokeWidth="0.0022" strokeLinecap="round" opacity="0.7">
        {INSIDERS.map((n) => {
          const [x0, y0] = P(n.pos);
          const [cx, cy] = P(arcControl(n.pos, TOKEN));
          return <path key={n.shillerId} d={`M${f(x0)} ${f(y0)} Q${f(cx)} ${f(cy)} ${f(tx)} ${f(ty)}`} />;
        })}
      </g>

      {/* token: rays, glow, ring, core */}
      <g stroke="#ff4f17" strokeWidth="0.0016" dangerouslySetInnerHTML={{ __html: rays.join("") }} />
      <circle cx={f(tx)} cy={f(ty)} r="0.34" fill="url(#hero-g-token)" />
      <circle cx={f(tx)} cy={f(ty)} r={f(ringR)} fill="none" stroke="#ff4f17" strokeWidth="0.0016" opacity="0.14" />
      <path d={ringPath} fill="none" stroke="#ff4f17" strokeWidth="0.0028" strokeLinecap="round" opacity="0.75" />
      <circle cx={f(tx)} cy={f(ty)} r="0.05" fill="#ff4f17" />
      <circle cx={f(tx)} cy={f(ty)} r="0.016" fill="#fff4ee" />

      {/* shiller nodes */}
      {NODES.map((n) => {
        const [x, y] = P(n.pos);
        return (
          <g key={n.shillerId}>
            {n.inside ? <circle cx={f(x)} cy={f(y)} r="0.09" fill="url(#hero-g-node)" /> : null}
            <circle cx={f(x)} cy={f(y)} r={n.inside ? "0.011" : "0.007"} fill="#ededeb" opacity={n.inside ? 1 : 0.55} />
          </g>
        );
      })}

      {/* labels — hidden on small screens where the still is scaled down */}
      <g
        className="hidden md:block"
        fontFamily="var(--font-geist-mono), ui-monospace, monospace"
        fontSize="0.03"
        fill="#ededeb"
      >
        {NODES.map((n) => {
          const [x, y] = P(n.pos);
          return (
            <text key={n.shillerId} x={f(x + 0.022)} y={f(y + 0.011)} opacity={n.inside ? 0.85 : 0.4}>
              {n.inside ? `${n.handle} · ${n.minute} min` : n.handle}
            </text>
          );
        })}
        <text x={f(tx + 0.09)} y={f(ty - 0.07)} fill="#ff4f17" opacity="0.95">
          {demoSignal.ticker} · {demoSignal.ageMinutes} min
        </text>
      </g>
    </svg>
  );
}
