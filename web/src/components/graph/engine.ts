import { caTier, caTierColor, type CaTier, type Outcome } from "@/lib/demo";
import { buildLayout } from "./layout";

/**
 * Canvas 2D renderer for the shiller → token graph. Framework-free: the React wrapper only creates it,
 * pushes focus / filter into it and listens for focus changes.
 * - assembles once when the canvas enters the viewport (skipped with reduced motion)
 * - rAF loop runs only while the canvas is on screen and the tab is visible
 * - auto-cycles the focus between shillers while the user is idle
 */

export type GraphEngine = {
  /** user-initiated focus (button, keyboard) */
  focus(id: string): void;
  /** true while the pointer is over the section or focus is inside it: no auto-cycling */
  setPaused(paused: boolean): void;
  /** the user touched the section: stop auto-cycling for good */
  markInteracted(): void;
  setFilter(active: ReadonlySet<Outcome>): void;
  setReduced(reduced: boolean): void;
  destroy(): void;
};

type Options = {
  initialFocus: string;
  reduced: boolean;
  /** `user` is false for auto-cycle changes */
  onFocus: (id: string, user: boolean) => void;
  /** Article mode starts without waiting for a scroll reveal. */
  startImmediately?: boolean;
  /** Disable pointer selection for uninterrupted article playback. */
  interactive?: boolean;
  /** Space inside the canvas for the article title and legend overlays. */
  verticalPadding?: number;
  /** Multiplier for label font sizes (article page: larger, readable in screenshots). */
  labelScale?: number;
  /** Keep the camera still (no pan/zoom towards the focused shiller) — calmer for recorded video. */
  staticCamera?: boolean;
  /** Time each shiller stays in focus before the auto-cycle moves on (default CYCLE_MS). */
  cycleMs?: number;
  /** Time constant of the focus highlight fade, ms (default 170; larger = smoother). */
  focusMs?: number;
};

const FALLBACK_COLORS: Record<Outcome, string> = {
  success: "#5be49b",
  rug: "#e5484d",
  dead: "#6b6b67",
  neutral: "#a3a39e",
  pending: "#ff4f17",
};
const FG = "#ededeb";
const MUTED = "#8a8a85";
const ACCENT = "#ff4f17";

const ASSEMBLE_MS = 3100;
const CYCLE_MS = 4200;

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);
const easeOut = (v: number) => 1 - Math.pow(1 - clamp01(v), 3);
const prog = (at: number, delay: number, dur: number) => easeOut((at - delay) / dur);

function cssVar(name: string, fallback: string) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch {
    return fallback;
  }
}

type Rect = [number, number, number, number];
const overlaps = (a: Rect, b: Rect) => a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];

export function createGraphEngine(canvas: HTMLCanvasElement, opts: Options): GraphEngine {
  const ctx = canvas.getContext("2d");
  const noop: GraphEngine = {
    focus() {},
    setPaused() {},
    markInteracted() {},
    setFilter() {},
    setReduced() {},
    destroy() {},
  };
  if (!ctx) return noop;

  const L = buildLayout();
  const nS = L.shillers.length;
  const nT = L.tokens.length;

  const colors: Record<Outcome, string> = {
    success: cssVar("--color-win", FALLBACK_COLORS.success),
    rug: cssVar("--color-rug", FALLBACK_COLORS.rug),
    dead: cssVar("--color-flat", FALLBACK_COLORS.dead),
    neutral: FALLBACK_COLORS.neutral,
    pending: cssVar("--color-accent", ACCENT),
  };
  const mono = cssVar("--font-mono", "ui-monospace, monospace");
  const caColors: Record<CaTier, string> = {
    low: cssVar(caTierColor.low.slice(4, -1), colors.success),
    mid: cssVar(caTierColor.mid.slice(4, -1), "#f2c94c"),
    high: cssVar(caTierColor.high.slice(4, -1), colors.rug),
  };

  let W = 0;
  let H = 0;
  let dpr = 1;
  let reduced = opts.reduced;
  let focusIdx = Math.max(
    0,
    L.shillers.findIndex((s) => s.id === opts.initialFocus),
  );
  const focusAmt = new Float32Array(nS);
  const outcomeDim: Record<Outcome, number> = { success: 0, rug: 0, dead: 0, neutral: 0, pending: 0 };
  let filter: ReadonlySet<Outcome> = new Set();
  let hoverToken = -1;
  let started = reduced || !!opts.startImmediately;
  let startT = opts.startImmediately ? performance.now() : 0;
  let inView = !!opts.startImmediately;
  let raf = 0;
  let last = 0;
  let interacted = false;
  let paused = false;
  let lastCycle = 0;
  let pending = 0;
  const cam = { x: 0, y: 0, z: 1 };

  // screen positions of the last frame (used for hit testing)
  const spx = new Float32Array(nS);
  const spy = new Float32Array(nS);
  const tpx = new Float32Array(nT);
  const tpy = new Float32Array(nT);
  const sAlpha = new Float32Array(nS);
  const tAlpha = new Float32Array(nT);

  focusAmt[focusIdx] = reduced ? 1 : 0;

  function resize() {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) return;
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = w;
    H = h;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    invalidate();
  }

  /** reduced motion: no loop, draw once per change */
  function invalidate() {
    if (!reduced || pending || !inView || document.hidden) return;
    pending = requestAnimationFrame((now) => {
      pending = 0;
      if (!inView || document.hidden) return;
      update(now, 16);
      render(now);
    });
  }

  function emitFocus(i: number, user: boolean) {
    if (user) interacted = true;
    if (i === focusIdx) return;
    focusIdx = i;
    invalidate();
    opts.onFocus(L.shillers[i].id, user);
  }

  function update(now: number, dt: number) {
    const k = reduced ? 1 : 1 - Math.exp(-dt / (opts.focusMs ?? 170));
    for (let i = 0; i < nS; i++) {
      const target = i === focusIdx && started ? 1 : 0;
      focusAmt[i] += (target - focusAmt[i]) * k;
      if (Math.abs(target - focusAmt[i]) < 0.002) focusAmt[i] = target;
    }
    const kd = reduced ? 1 : 1 - Math.exp(-dt / 160);
    (Object.keys(outcomeDim) as Outcome[]).forEach((o) => {
      const target = filter.size > 0 && !filter.has(o) ? 1 : 0;
      outcomeDim[o] += (target - outcomeDim[o]) * kd;
    });
    if (!opts.staticCamera) {
      const f = L.shillers[focusIdx];
      const kc = reduced ? 1 : 1 - Math.exp(-dt / 520);
      cam.x += (f.x * 0.1 - cam.x) * kc;
      cam.y += (f.y * 0.1 - cam.y) * kc;
      cam.z += (1.05 - cam.z) * kc;
    }

    if (
      started &&
      !reduced &&
      !interacted &&
      !paused &&
      now - startT > ASSEMBLE_MS &&
      now - lastCycle > (opts.cycleMs ?? CYCLE_MS)
    ) {
      lastCycle = now;
      emitFocus((focusIdx + 1) % nS, false);
    }
  }

  function render(now: number) {
    if (!ctx || !W || !H) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (!started) return;

    const at = reduced ? 1e9 : now - startT;
    const small = W < 520;
    const padX = small ? 42 : 76;
    const padY = Math.min(opts.verticalPadding ?? (small ? 34 : 44), H / 4);
    const hw = W / 2 - padX;
    const hh = H / 2 - padY;
    const minHalf = Math.min(W, H) / 2;
    const ls = opts.labelScale ?? 1;
    const fs = (small ? 9.5 : 11) * ls;
    const drift = reduced ? 0 : 1;

    // ---- positions -------------------------------------------------------------------------
    for (let i = 0; i < nS; i++) {
      const s = L.shillers[i];
      const e = prog(at, i * 110, 900);
      sAlpha[i] = e;
      spx[i] = W / 2 + (s.x - cam.x) * cam.z * hw + s.sx * (1 - e);
      spy[i] = H / 2 + (s.y - cam.y) * cam.z * hh + s.sy * (1 - e);
    }
    for (let i = 0; i < nT; i++) {
      const t = L.tokens[i];
      const e = prog(at, 550 + i * 60, 800);
      tAlpha[i] = e;
      tpx[i] = W / 2 + (t.x - cam.x) * cam.z * hw + t.sx * (1 - e);
      tpy[i] = H / 2 + (t.y - cam.y) * cam.z * hh + t.sy * (1 - e);
    }
    const tokenLink = (ti: number) => {
      let m = 0;
      for (const si of L.tokens[ti].shillers) m = Math.max(m, focusAmt[si]);
      return m;
    };
    const tokenDimAlpha = (o: Outcome) => 1 - 0.86 * outcomeDim[o];

    // ---- history clouds ----------------------------------------------------------------------
    for (let i = 0; i < nS; i++) {
      const s = L.shillers[i];
      const bx = W / 2 + (s.x - cam.x) * cam.z * hw;
      const by = H / 2 + (s.y - cam.y) * cam.z * hh;
      const boost = 1 + focusAmt[i] * 0.55;
      for (const p of s.cloud) {
        const e = prog(at, 150 + p.delay * 900, 1000);
        if (e <= 0) continue;
        const x = bx + p.dx * minHalf * cam.z + Math.sin(now * 0.0005 + p.phase) * 2.4 * drift + p.sx * (1 - e);
        const y = by + p.dy * minHalf * cam.z + Math.cos(now * 0.00043 + p.phase * 1.3) * 2.4 * drift + p.sy * (1 - e);
        ctx.globalAlpha = Math.min(1, p.alpha * e * boost) * tokenDimAlpha(p.outcome);
        ctx.fillStyle = colors[p.outcome];
        ctx.beginPath();
        ctx.arc(x, y, p.size, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // ---- edges (curved, thin) -----------------------------------------------------------------
    ctx.lineWidth = 1;
    ctx.strokeStyle = FG;
    L.edges.forEach((ed, idx) => {
      const e = reduced ? 1 : easeOut((at - (1100 + idx * 30)) / 700);
      if (e <= 0) return;
      const t = L.tokens[ed.t];
      const x1 = spx[ed.s];
      const y1 = spy[ed.s];
      const x2 = tpx[ed.t];
      const y2 = tpy[ed.t];
      const cx = (x1 + x2) / 2 - (y2 - y1) * ed.bend;
      const cy = (y1 + y2) / 2 + (x2 - x1) * ed.bend;
      const hov = hoverToken === ed.t;
      let a = hov ? 0.55 : 0.11;
      a *= tokenDimAlpha(t.outcome);
      a *= 1 - focusAmt[ed.s] * 0.55;
      ctx.globalAlpha = a;
      ctx.strokeStyle = hov ? colors[t.outcome] : FG;
      if (e < 1) ctx.setLineDash([Math.hypot(x2 - x1, y2 - y1) * 1.25 * e, 1e5]);
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.quadraticCurveTo(cx, cy, x2, y2);
      ctx.stroke();
      if (e < 1) ctx.setLineDash([]);
    });

    // ---- focus "hair": rays to history points + straight lines to tokens -------------------------
    for (let i = 0; i < nS; i++) {
      const a = focusAmt[i] * sAlpha[i];
      if (a < 0.01) continue;
      const s = L.shillers[i];
      const bx = spx[i];
      const by = spy[i];
      ctx.lineWidth = 0.6;
      ctx.strokeStyle = ACCENT;
      ctx.globalAlpha = 0.13 * a;
      ctx.beginPath();
      for (const p of s.cloud) {
        if (outcomeDim[p.outcome] > 0.5) continue;
        const x = bx + p.dx * minHalf * cam.z + Math.sin(now * 0.0005 + p.phase) * 2.4 * drift;
        const y = by + p.dy * minHalf * cam.z + Math.cos(now * 0.00043 + p.phase * 1.3) * 2.4 * drift;
        ctx.moveTo(bx, by);
        ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.lineWidth = 1;
      for (const ti of s.tokens) {
        const t = L.tokens[ti];
        ctx.globalAlpha = 0.75 * a * tokenDimAlpha(t.outcome) * tAlpha[ti];
        ctx.strokeStyle = colors[t.outcome];
        ctx.beginPath();
        ctx.moveTo(bx, by);
        ctx.lineTo(tpx[ti], tpy[ti]);
        ctx.stroke();
      }
    }

    // ---- tokens -----------------------------------------------------------------------------
    for (let i = 0; i < nT; i++) {
      const t = L.tokens[i];
      if (tAlpha[i] <= 0) continue;
      const link = tokenLink(i);
      const hov = hoverToken === i ? 1 : 0;
      const dim = tokenDimAlpha(t.outcome);
      const r = t.r * (0.5 + 0.5 * tAlpha[i]) * (1 + 0.18 * Math.max(link, hov));
      const x = tpx[i];
      const y = tpy[i];
      const base = (0.62 + 0.38 * Math.max(link, hov)) * dim * tAlpha[i];
      const g = ctx.createRadialGradient(x, y, 0, x, y, r * 3.4);
      g.addColorStop(0, colors[t.outcome]);
      g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.globalAlpha = 0.28 * base;
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x, y, r * 3.4, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = base;
      ctx.fillStyle = colors[t.outcome];
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }

    // ---- shillers ---------------------------------------------------------------------------
    ctx.font = `${fs}px ${mono}`;
    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    const labelRects: Rect[] = [];
    const metricLabels: { i: number; x: number; y: number }[] = [];
    for (let i = 0; i < nS; i++) {
      const s = L.shillers[i];
      const a = sAlpha[i];
      if (a <= 0) continue;
      const fa = focusAmt[i];
      const x = spx[i];
      const y = spy[i];
      const r = s.r * (0.4 + 0.6 * a);
      // glow
      const g = ctx.createRadialGradient(x, y, 0, x, y, r * 6);
      g.addColorStop(0, "rgba(237,237,235,0.30)");
      g.addColorStop(0.35, "rgba(237,237,235,0.08)");
      g.addColorStop(1, "rgba(237,237,235,0)");
      ctx.globalAlpha = a;
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x, y, r * 6, 0, Math.PI * 2);
      ctx.fill();
      if (fa > 0.01) {
        const g2 = ctx.createRadialGradient(x, y, 0, x, y, r * 7);
        g2.addColorStop(0, "rgba(255,79,23,0.40)");
        g2.addColorStop(1, "rgba(255,79,23,0)");
        ctx.globalAlpha = fa * a;
        ctx.fillStyle = g2;
        ctx.beginPath();
        ctx.arc(x, y, r * 7, 0, Math.PI * 2);
        ctx.fill();
      }
      // core
      ctx.globalAlpha = a;
      ctx.fillStyle = FG;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.lineWidth = 1;
      ctx.strokeStyle = FG;
      ctx.globalAlpha = 0.3 * a;
      ctx.beginPath();
      ctx.arc(x, y, r + 3.5, 0, Math.PI * 2);
      ctx.stroke();
      // focus ring
      if (fa > 0.01) {
        ctx.strokeStyle = ACCENT;
        ctx.lineWidth = 1.6;
        ctx.globalAlpha = fa;
        ctx.beginPath();
        ctx.arc(x, y, r + 7.5, 0, Math.PI * 2);
        ctx.stroke();
        ctx.lineWidth = 1;
        ctx.globalAlpha = 0.7 * fa;
        ctx.setLineDash([2, 5]);
        ctx.lineDashOffset = -(now * 0.012) * drift;
        ctx.beginPath();
        ctx.arc(x, y, r + 14, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.lineDashOffset = 0;
      }
      // handle label
      const label = s.handle;
      const showMetrics = !small || i === focusIdx;
      const metricFs = (small ? 8.5 : 9.5) * ls;
      const ly = Math.min(H - fs / 2 - 4 - (showMetrics ? metricFs + 5 : 0), y + r + (fa > 0.01 ? 25 : 17));
      const tw = ctx.measureText(label).width;
      const lx = Math.max(tw / 2 + 4, Math.min(W - tw / 2 - 4, x));
      labelRects.push([lx - tw / 2 - 2, ly - fs / 2 - 1, lx + tw / 2 + 2, ly + fs / 2 + 1]);
      ctx.globalAlpha = a * (1 - fa);
      ctx.fillStyle = MUTED;
      ctx.fillText(label, lx, ly);
      ctx.globalAlpha = a * fa;
      ctx.fillStyle = FG;
      ctx.fillText(label, lx, ly);
      if (showMetrics) metricLabels.push({ i, x, y: ly + fs / 2 + metricFs / 2 + 4 });
    }

    // Reserve all handles first, then place metrics with the focused shiller first.
    const metricFs = (small ? 8.5 : 9.5) * ls;
    ctx.font = `${metricFs}px ${mono}`;
    ctx.textAlign = "left";
    metricLabels.sort((a, b) => Number(b.i === focusIdx) - Number(a.i === focusIdx));
    for (const label of metricLabels) {
      const s = L.shillers[label.i];
      const smart = small ? `◆ ${s.smarts}  ·  ` : `SMARTS ${s.smarts}  ·  `;
      const ca = `CA ${s.ca}`;
      const smartWidth = ctx.measureText(smart).width;
      const width = smartWidth + ctx.measureText(ca).width;
      const x = Math.max(4, Math.min(W - width - 4, label.x - width / 2));
      const rect: Rect = [x - 2, label.y - metricFs / 2 - 1, x + width + 2, label.y + metricFs / 2 + 1];
      if (labelRects.some((r) => overlaps(r, rect))) continue;
      labelRects.push(rect);
      const brightness = 0.6 + focusAmt[label.i] * 0.4;
      ctx.globalAlpha = sAlpha[label.i] * brightness;
      ctx.fillStyle = FG;
      ctx.fillText(smart, x, label.y);
      ctx.fillStyle = caColors[caTier(s.ca)];
      ctx.fillText(ca, x + smartWidth, label.y);
    }

    // ---- token labels (focused shiller's tokens + hovered token) ----------------------------------
    ctx.font = `${(small ? 9 : 10.5) * ls}px ${mono}`;
    ctx.textBaseline = "middle";
    const order: number[] = [];
    if (hoverToken >= 0) order.push(hoverToken);
    for (const ti of L.shillers[focusIdx].tokens) if (ti !== hoverToken) order.push(ti);
    const tfs = (small ? 9 : 10.5) * ls;
    for (const ti of order) {
      const t = L.tokens[ti];
      const link = tokenLink(ti);
      const isHover = ti === hoverToken;
      const vis = Math.max(link, isHover ? 1 : 0) * tAlpha[ti] * tokenDimAlpha(t.outcome);
      if (vis < 0.05) continue;
      const tw = ctx.measureText(t.ticker).width;
      const x = tpx[ti];
      const y = tpy[ti];
      const fx = spx[focusIdx];
      const fy = spy[focusIdx];
      let dx = x - fx;
      let dy = y - fy;
      const dl = Math.hypot(dx, dy) || 1;
      dx /= dl;
      dy /= dl;
      const gap = t.r + 5;
      const cands: [number, number][] = [
        [dx, dy],
        [dy, -dx],
        [-dy, dx],
        [0, -1],
        [0, 1],
        [1, 0],
        [-1, 0],
        [-dx, -dy],
      ];
      let placed = false;
      for (const [cx, cy] of cands) {
        const anchorX = x + cx * gap;
        const anchorY = y + cy * gap;
        // text box: left-aligned when pointing right, right-aligned when pointing left, centred otherwise
        let x0 = cx > 0.35 ? anchorX : cx < -0.35 ? anchorX - tw : anchorX - tw / 2;
        let y0 = cy > 0.35 ? anchorY : cy < -0.35 ? anchorY - tfs : anchorY - tfs / 2;
        x0 = Math.max(3, Math.min(W - tw - 3, x0));
        y0 = Math.max(2, Math.min(H - tfs - 2, y0));
        const rect: Rect = [x0 - 2, y0 - 1, x0 + tw + 2, y0 + tfs + 1];
        if (!isHover && labelRects.some((r) => overlaps(r, rect))) continue;
        labelRects.push(rect);
        ctx.textAlign = "left";
        ctx.globalAlpha = vis;
        ctx.fillStyle = isHover ? FG : colors[t.outcome];
        ctx.fillText(t.ticker, x0, y0 + tfs / 2);
        placed = true;
        break;
      }
      if (!placed) continue;
    }
    ctx.textAlign = "center";
    ctx.globalAlpha = 1;
  }

  function tick(now: number) {
    raf = 0;
    if (!inView || document.hidden) return;
    const dt = last ? Math.min(60, now - last) : 16;
    last = now;
    update(now, dt);
    render(now);
    raf = requestAnimationFrame(tick);
  }

  function sync() {
    if (reduced) {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      invalidate();
      return;
    }
    const should = inView && !document.hidden;
    if (should && !raf) {
      last = 0;
      lastCycle = performance.now();
      raf = requestAnimationFrame(tick);
    } else if (!should && raf) {
      cancelAnimationFrame(raf);
      raf = 0;
    }
  }

  // ---- observers / events ---------------------------------------------------------------------------
  const io = new IntersectionObserver(
    (entries) => {
      for (const en of entries) {
        inView = en.isIntersecting;
        if (en.isIntersecting && !started && en.intersectionRatio >= 0.2) {
          started = true;
          startT = performance.now();
          lastCycle = startT;
        }
      }
      sync();
    },
    { threshold: [0, 0.2] },
  );
  io.observe(canvas);

  const ro = new ResizeObserver(resize);
  ro.observe(canvas);
  resize();

  const onVis = () => sync();
  document.addEventListener("visibilitychange", onVis);

  function pick(x: number, y: number): { kind: "s" | "t"; i: number } | null {
    let best = -1;
    let bestD = Infinity;
    for (let i = 0; i < nS; i++) {
      if (sAlpha[i] < 0.5) continue;
      const d = Math.hypot(spx[i] - x, spy[i] - y);
      if (d < L.shillers[i].r + 14 && d < bestD) {
        bestD = d;
        best = i;
      }
    }
    if (best >= 0) return { kind: "s", i: best };
    for (let i = 0; i < nT; i++) {
      if (tAlpha[i] < 0.5) continue;
      const d = Math.hypot(tpx[i] - x, tpy[i] - y);
      if (d < L.tokens[i].r + 7 && d < bestD) {
        bestD = d;
        best = i;
      }
    }
    return best >= 0 ? { kind: "t", i: best } : null;
  }

  function localXY(ev: PointerEvent) {
    const rect = canvas.getBoundingClientRect();
    return [ev.clientX - rect.left, ev.clientY - rect.top] as const;
  }

  function onMove(ev: PointerEvent) {
    const [x, y] = localXY(ev);
    const hit = pick(x, y);
    const nextHover = hit && hit.kind === "t" ? hit.i : -1;
    if (nextHover !== hoverToken) {
      hoverToken = nextHover;
      invalidate();
    }
    canvas.style.cursor = hit ? "pointer" : "default";
    if (ev.pointerType === "mouse" && hit && hit.kind === "s") emitFocus(hit.i, true);
  }
  function onDown(ev: PointerEvent) {
    const [x, y] = localXY(ev);
    const hit = pick(x, y);
    interacted = true;
    if (!hit) return;
    if (hit.kind === "s") emitFocus(hit.i, true);
    else {
      hoverToken = hit.i;
      invalidate();
    }
  }
  function onLeave() {
    if (hoverToken !== -1) {
      hoverToken = -1;
      invalidate();
    }
  }
  if (opts.interactive !== false) {
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointerleave", onLeave);
  }
  sync();

  return {
    focus(id) {
      const i = L.shillers.findIndex((s) => s.id === id);
      if (i < 0) return;
      interacted = true;
      if (i !== focusIdx) {
        focusIdx = i;
        invalidate();
      }
    },
    setPaused(p) {
      paused = p;
    },
    markInteracted() {
      interacted = true;
    },
    setFilter(active) {
      const changed = active.size !== filter.size || Array.from(active).some((o) => !filter.has(o));
      if (!changed) return;
      filter = active;
      invalidate();
    },
    setReduced(r) {
      reduced = r;
      if (r && !started) {
        started = true;
        startT = performance.now();
      }
      sync();
    },
    destroy() {
      if (raf) cancelAnimationFrame(raf);
      if (pending) cancelAnimationFrame(pending);
      raf = 0;
      pending = 0;
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVis);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointerleave", onLeave);
    },
  };
}
