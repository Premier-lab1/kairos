"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { AreaSeries, ColorType, CrosshairMode, HistogramSeries, LineStyle, createChart, type Time, type UTCTimestamp } from "lightweight-charts";
import { Avatar } from "@/components/ui/Avatar";
import { outcomeColor, outcomeLabel } from "@/lib/demo";
import { useReducedMotion } from "@/lib/useReducedMotion";
import type { CallChartProps, MarketPoint } from "./types";

const utc = (t: Time) => typeof t === "number" ? new Date(t * 1000).toISOString().slice(11, 16) : String(t);
const ts = (t: number) => t as UTCTimestamp;
const MAX_LIFT = 50 + 36 * 2;
const ring = (tone?: string) => tone === "good" ? "var(--color-win)" : tone === "bad" ? "var(--color-rug)" : "rgba(237,237,235,.4)";
const eventColor = { up: "#5be49b", down: "#e5484d", accent: "#ff4f17" } as const;
const trendColor = { up: "#5be49b", down: "#e5484d", flat: "#8a8a85" } as const;
function interpolate(points: MarketPoint[], t: number) {
  if (!points.length || t < points[0].t || t > points[points.length - 1].t) return null;
  let lo = 0, hi = points.length - 1;
  while (lo < hi) { const mid = (lo + hi) >>> 1; if (points[mid].t < t) lo = mid + 1; else hi = mid; }
  const b = points[lo], a = points[Math.max(0, lo - 1)];
  return a.t === b.t ? b.mcapSol : a.mcapSol + (b.mcapSol - a.mcapSol) * (t - a.t) / (b.t - a.t);
}

export function CallChart({ points, volume, calls, height, playOnView = false, marker, outcome, variant = "classic", trend = "up", events, ariaLabel }: CallChartProps) {
  const wrapper = useRef<HTMLDivElement>(null), canvas = useRef<HTMLDivElement>(null), overlay = useRef<HTMLDivElement>(null), signal = useRef<HTMLDivElement>(null), tooltip = useRef<HTMLDivElement>(null), badge = useRef<HTMLDivElement>(null);
  const played = useRef(false);
  const reduced = useReducedMotion();
  const glow = variant === "glow";
  const lineColor = glow ? trendColor[trend] : "#ff4f17";
  useEffect(() => {
    if (!wrapper.current || !canvas.current) return;
    const root = wrapper.current;
    const chart = createChart(canvas.current, {
      width: root.clientWidth, height: height ?? (root.clientWidth < 640 ? 360 : 420),
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8a8a85", fontFamily: getComputedStyle(root).getPropertyValue("--font-geist-mono").trim() || "monospace", fontSize: 10 },
      grid: { vertLines: { color: glow ? "transparent" : "#181818" }, horzLines: { color: "#181818", style: glow ? LineStyle.Dashed : LineStyle.Solid } },
      rightPriceScale: { borderColor: "#262626", scaleMargins: { top: .26, bottom: .06 } },
      // Fixed window: playback must not scroll the time axis towards the newest bar.
      timeScale: { borderColor: "#262626", timeVisible: true, secondsVisible: false, tickMarkFormatter: utc, shiftVisibleRangeOnNewBar: false },
      localization: { timeFormatter: (t: Time) => `${utc(t)} UTC` },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { color: "#77777766" }, horzLine: { color: "#77777766" } },
      handleScroll: !playOnView, handleScale: !playOnView,
    });
    // Glow: a wide translucent copy of the line under the real one.
    const halo = glow ? chart.addSeries(AreaSeries, { lineColor: `${lineColor}2e`, topColor: `${lineColor}14`, bottomColor: `${lineColor}00`, lineWidth: 4, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }) : null;
    const area = chart.addSeries(AreaSeries, { lineColor, topColor: `${lineColor}${glow ? "4d" : "38"}`, bottomColor: `${lineColor}00`, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    // Volume lives in its own pane under the price, so it never stretches the market-cap axis.
    const buys = chart.addSeries(HistogramSeries, { color: "#5be49b66", priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "volume" } }, 1);
    const sells = chart.addSeries(HistogramSeries, { color: "#e5484d66", priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "volume" } }, 1);
    // keep this scale visible: a hidden price scale on a second pane throws in lightweight-charts 5 layout
    buys.priceScale().applyOptions({ scaleMargins: { top: .1, bottom: .1 } });
    chart.panes()[1]?.setHeight(64);
    const sorted = [...new Map(points.filter(p => Number.isFinite(p.t) && Number.isFinite(p.mcapSol)).map(p => [p.t, p])).values()].sort((a, b) => a.t - b.t);
    const samples = new Map(sorted.map(p => [p.t, p.mcapSol]));
    const dots = events ?? [];
    // Add exact call/event times so timeToCoordinate resolves them between market samples.
    for (const t of [...calls.map(c => c.t), ...dots.map(e => e.t), ...(marker ? [marker.t] : [])]) { const value = interpolate(sorted, t); if (value !== null) samples.set(t, value); }
    const data = [...samples].sort(([a], [b]) => a - b).map(([t, value]) => ({ time: ts(t), value }));
    const bars = [...new Map((volume ?? []).filter(v => Number.isFinite(v.t) && Number.isFinite(v.buySol) && Number.isFinite(v.sellSol)).map(v => [v.t, v])).values()].sort((a, b) => a.t - b.t);
    let playhead = -Infinity, layoutFrame = 0;
    // Many calls: labels only on hover/focus, otherwise they pile up.
    const crowded = calls.length > 8;
    const place = () => {
      const width = chart.timeScale().width();
      const occupied: { x: number; y: number; el: HTMLElement; labelWidth: number }[] = [];
      overlay.current?.querySelectorAll<HTMLElement>("[data-call]").forEach((el, i) => {
        const call = calls[i], x = chart.timeScale().timeToCoordinate(ts(call.t)), value = interpolate(sorted, call.t);
        const y = value === null ? null : area.priceToCoordinate(value);
        const visible = x !== null && y !== null && x >= 16 && x <= width - 16 && y >= 45 && y <= root.clientHeight - 30 && call.t <= playhead;
        el.dataset.visible = String(visible); el.style.visibility = visible ? "visible" : "hidden"; el.tabIndex = visible ? 0 : -1;
        if (!visible || x === null || y === null) return;
        let lift = 50;
        const collides = () => occupied.some(p => Math.abs(p.x - x) < 35 && Math.abs(p.y - (y - lift)) < 34);
        while (collides() && lift < MAX_LIFT && y - lift > 70) lift += 36;
        // Crowded spot: at most three avatars stacked over one point; the rest stay in the mentions list.
        if (collides()) { el.dataset.visible = "false"; el.style.visibility = "hidden"; el.tabIndex = -1; return; }
        const top = y - lift, labelWidth = Math.min(150, call.username.length * 6 + 20);
        el.dataset.label = String(!crowded && x + labelWidth < width && !occupied.some(p => Math.abs(p.x - x) < labelWidth && Math.abs(p.y - top) < 58));
        el.style.left = `${x - 15}px`; el.style.top = `${top}px`;
        const stem = el.querySelector<HTMLElement>("[data-stem]"); if (stem) stem.style.height = `${lift - 30}px`;
        const anchor = el.querySelector<HTMLElement>("[data-anchor]"); if (anchor) anchor.style.top = `${lift - 3}px`;
        occupied.push({ x, y: top, el, labelWidth });
      });
      // Check all neighbours, including avatars encountered later in the input.
      for (const p of occupied) {
        if (occupied.some(q => q !== p && Math.abs(q.y - p.y) < 58 && q.x > p.x && q.x - p.x < p.labelWidth)) p.el.dataset.label = "false";
      }
      overlay.current?.querySelectorAll<HTMLElement>("[data-event]").forEach((el, i) => {
        const event = dots[i], x = chart.timeScale().timeToCoordinate(ts(event.t)), value = interpolate(sorted, event.t);
        const y = value === null ? null : area.priceToCoordinate(value);
        const visible = x !== null && y !== null && x >= 0 && x <= width && event.t <= playhead;
        el.style.visibility = visible ? "visible" : "hidden";
        if (visible) { el.style.left = `${x - 4}px`; el.style.top = `${y - 4}px`; }
      });
      if (signal.current && marker) {
        const x = chart.timeScale().timeToCoordinate(ts(marker.t));
        signal.current.style.display = x !== null && x >= 0 && x < width && marker.t <= playhead ? "block" : "none";
        signal.current.style.left = `${x ?? 0}px`;
        const pill = signal.current.firstElementChild as HTMLElement | null;
        if (pill) pill.style.transform = x !== null && x > width - 125 ? "translateX(-100%)" : "none";
      }
      if (badge.current) {
        // Floating value on the newest drawn point.
        let last: MarketPoint | undefined; for (let i = sorted.length - 1; i >= 0; i--) if (sorted[i].t <= playhead) { last = sorted[i]; break; }
        const x = last ? chart.timeScale().timeToCoordinate(ts(last.t)) : null, y = last ? area.priceToCoordinate(last.mcapSol) : null;
        const visible = glow && last && x !== null && y !== null && x >= 0 && x <= width;
        badge.current.style.display = visible ? "block" : "none";
        if (visible && last) {
          badge.current.style.left = `${x}px`; badge.current.style.top = `${y}px`;
          const pill = badge.current.querySelector<HTMLElement>("[data-pill]");
          if (pill) { pill.textContent = `${last.mcapSol >= 1000 ? `${(last.mcapSol / 1000).toFixed(1)}K` : last.mcapSol.toFixed(last.mcapSol >= 100 ? 0 : 1)} SOL`; pill.style.transform = x < 120 ? "translate(14px, -50%)" : "translate(calc(-100% - 14px), -50%)"; }
        }
      }
    };
    const schedule = () => { cancelAnimationFrame(layoutFrame); layoutFrame = requestAnimationFrame(place); };
    const span = data.length ? { from: data[0].time, to: data[data.length - 1].time } : null;
    const render = (count: number) => {
      playhead = data[Math.max(0, count - 1)]?.time ?? -Infinity;
      const line = data.map((p, i) => i < count ? p : { time: p.time });
      area.setData(line); halo?.setData(line);
      buys.setData(bars.map(v => v.t <= playhead ? { time: ts(v.t), value: v.buySol } : { time: ts(v.t) }));
      sells.setData(bars.map(v => v.t <= playhead ? { time: ts(v.t), value: -v.sellSol } : { time: ts(v.t) }));
      if (span && playOnView) chart.timeScale().setVisibleRange(span);
      schedule();
    };
    const animate = playOnView && !reduced && !played.current && data.length > 1;
    render(animate ? 1 : data.length);
    if (!playOnView) {
      chart.timeScale().fitContent();
      // Room for edge avatars (and the value badge).
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range) chart.timeScale().setVisibleLogicalRange({ from: range.from - 6, to: range.to + (glow ? 10 : 6) });
    }
    if (reduced && playOnView) played.current = true;
    const move: Parameters<typeof chart.subscribeCrosshairMove>[0] = (event) => {
      const tip = tooltip.current; if (!tip) return;
      if (!event.point || typeof event.time !== "number" || event.time > playhead) { tip.hidden = true; return; }
      const t = event.time, value = interpolate(sorted, t);
      if (value === null) { tip.hidden = true; return; }
      const bar = bars.find(v => t >= v.t && t < v.t + 60);
      tip.textContent = `${utc(t)} UTC · ${value.toFixed(2)} SOL\nBuy ${bar?.buySol.toFixed(2) ?? "—"} · Sell ${bar?.sellSol.toFixed(2) ?? "—"} SOL`;
      tip.hidden = false; tip.style.left = `${Math.max(6, Math.min(event.point.x + 12, root.clientWidth - 246))}px`;
      tip.style.top = `${Math.max(44, Math.min(event.point.y - 65, root.clientHeight - 90))}px`;
    };
    chart.subscribeCrosshairMove(move); chart.timeScale().subscribeVisibleLogicalRangeChange(schedule); chart.timeScale().subscribeSizeChange(schedule);
    const resize = new ResizeObserver(() => { chart.resize(root.clientWidth, height ?? (root.clientWidth < 640 ? 360 : 420)); schedule(); }); resize.observe(root);
    let frame = 0, elapsed = 0, previous = 0, lastCount = 1, inView = false;
    const tick = (now: number) => {
      if (!inView || document.hidden || elapsed >= 5000) { previous = 0; return; }
      if (previous) elapsed += now - previous; previous = now;
      const count = Math.min(data.length, 1 + Math.floor(elapsed / 5000 * (data.length - 1)));
      if (count !== lastCount) { render(count); lastCount = count; }
      if (elapsed < 5000) frame = requestAnimationFrame(tick); else played.current = true;
    };
    const resume = () => { cancelAnimationFrame(frame); previous = 0; if (animate && inView && !document.hidden && elapsed < 5000) frame = requestAnimationFrame(tick); };
    const observer = new IntersectionObserver(([entry]) => { inView = entry.intersectionRatio >= .3; resume(); }, { threshold: [0, .3] });
    if (animate) observer.observe(root);
    document.addEventListener("visibilitychange", resume); root.addEventListener("pointermove", schedule); root.addEventListener("wheel", schedule, { passive: true });
    return () => {
      cancelAnimationFrame(frame); cancelAnimationFrame(layoutFrame); observer.disconnect(); resize.disconnect();
      document.removeEventListener("visibilitychange", resume); root.removeEventListener("pointermove", schedule); root.removeEventListener("wheel", schedule);
      chart.unsubscribeCrosshairMove(move); chart.timeScale().unsubscribeVisibleLogicalRangeChange(schedule); chart.timeScale().unsubscribeSizeChange(schedule); chart.remove();
    };
  }, [points, volume, calls, marker, height, playOnView, reduced, glow, lineColor, events]);
  return (
    <div ref={wrapper} role="img" aria-label={ariaLabel} className={`relative isolate w-full min-w-0 overflow-hidden rounded-xl border border-line ${glow ? "bg-ink" : "bg-ink-2"}`} style={height ? { height } : undefined}>
      <div ref={canvas} className="w-full" />
      <span className="pointer-events-none absolute top-3 left-3 z-10 font-mono text-[10px] text-muted">MCAP / SOL · UTC</span>
      {outcome && <span className="absolute top-3 right-3 z-20 rounded-full border bg-ink-2 px-2 py-0.5 font-mono text-[10px]" style={{ color: outcomeColor[outcome] }}>{outcomeLabel[outcome]}</span>}
      <div ref={signal} className="pointer-events-none absolute top-16 bottom-7 z-10 hidden border-l border-dashed border-accent/70"><span className="absolute -top-6 left-0 whitespace-nowrap rounded-full bg-accent px-2 py-1 font-mono text-[9px] text-ink">{marker?.label}</span></div>
      <div ref={overlay} className="pointer-events-none absolute inset-0 z-20">
        {calls.map((call, i) => {
          const content = <><Avatar username={call.username} size={30} remote={Boolean(call.href)} src={call.avatarUrl} ring={ring(call.tone)} /><span data-stem className="kc-stem" />{glow && <span data-anchor aria-hidden className="absolute left-3 size-1.5 rounded-full bg-accent shadow-[0_0_6px_#ff4f17]" />}<span className="kc-label">@{call.username.replace(/^@/, "")}</span></>;
          const props = { "data-call": true, className: "kc-call", style: { color: ring(call.tone) }, "aria-label": `@${call.username.replace(/^@/, "")} call at ${utc(ts(call.t))} UTC` };
          return call.href ? /^https?:\/\//i.test(call.href) ? <a key={`${call.t}-${i}`} {...props} href={call.href} target="_blank" rel="noopener noreferrer">{content}</a> : <Link key={`${call.t}-${i}`} {...props} href={call.href}>{content}</Link> : <button key={`${call.t}-${i}`} {...props} type="button">{content}</button>;
        })}
        {(events ?? []).map((event, i) => <span key={`${event.t}-${i}`} data-event title={event.label} className="pointer-events-auto absolute z-10 size-2 rounded-full border border-ink" style={{ visibility: "hidden", background: eventColor[event.tone], boxShadow: `0 0 6px ${eventColor[event.tone]}` }} />)}
      </div>
      {glow && <div ref={badge} className="pointer-events-none absolute z-20 hidden"><span aria-hidden className="absolute -top-[5px] -left-[5px] block size-2.5 rounded-full" style={{ background: lineColor, boxShadow: `0 0 10px 2px ${lineColor}99` }} /><span data-pill className="absolute top-0 left-0 whitespace-nowrap rounded-md border bg-ink/95 px-2.5 py-1 font-mono text-sm" style={{ borderColor: lineColor, color: lineColor, boxShadow: `0 0 14px ${lineColor}40` }} /></div>}
      <div ref={tooltip} hidden className="pointer-events-none absolute z-30 max-w-[calc(100%-12px)] whitespace-pre-line rounded-md border border-line bg-ink/95 px-3 py-2 font-mono text-[10px] leading-relaxed text-fg shadow-lg" />
      {!points.length && <p className="absolute inset-0 grid place-items-center font-mono text-xs text-muted">No market-cap data yet</p>}
    </div>
  );
}
