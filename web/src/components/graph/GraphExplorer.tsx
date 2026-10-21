"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Avatar } from "@/components/ui/Avatar";
import { DemoTag } from "@/components/ui/DemoTag";
import { useReducedMotion } from "@/lib/useReducedMotion";
import {
  caTier,
  caTierColor,
  graphTokens,
  outcomeColor,
  outcomeLabel,
  shillerById,
  shillers,
  type Outcome,
  type Shiller,
} from "@/lib/demo";
import { createGraphEngine, type GraphEngine } from "./engine";
import { buildLayout } from "./layout";
import { GraphMetricsLegend } from "./GraphLegend";

const FILTER_OUTCOMES: Outcome[] = ["success", "rug", "dead", "neutral"];

const pct = (v: number) => `${Math.round(v * 100)}%`;

function formatFollowers(n: number) {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

export function GraphExplorer() {
  const reduced = useReducedMotion();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<GraphEngine | null>(null);
  const reducedRef = useRef(reduced);
  const [focusId, setFocusId] = useState(shillers[0].id);
  const [active, setActive] = useState<Outcome[]>([]);
  /** once true, auto-cycling is off for good and focus changes are announced to screen readers */
  const [interacted, setInteracted] = useState(false);

  const counts = useMemo(() => {
    const c: Record<Outcome, number> = { success: 0, rug: 0, dead: 0, neutral: 0, pending: 0 };
    for (const t of graphTokens) c[t.outcome] += 1;
    return c;
  }, []);
  const cloudCount = useMemo(() => buildLayout().cloudCount, []);
  const avgHit = useMemo(() => shillers.reduce((a, s) => a + s.hitRate, 0) / shillers.length, []);
  const avgRug = useMemo(() => shillers.reduce((a, s) => a + s.rugRate, 0) / shillers.length, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const engine = createGraphEngine(canvas, {
      initialFocus: shillers[0].id,
      reduced: reducedRef.current,
      onFocus: (id, user) => {
        setFocusId(id);
        if (user) setInteracted(true);
      },
    });
    engineRef.current = engine;
    return () => {
      engine.destroy();
      engineRef.current = null;
    };
  }, []);

  useEffect(() => {
    reducedRef.current = reduced;
    engineRef.current?.setReduced(reduced);
  }, [reduced]);

  useEffect(() => {
    engineRef.current?.setFilter(new Set(active));
  }, [active]);

  const markInteracted = useCallback(() => {
    setInteracted(true);
    engineRef.current?.markInteracted();
  }, []);

  const selectShiller = useCallback((id: string) => {
    markInteracted();
    setFocusId(id);
    engineRef.current?.focus(id);
  }, [markInteracted]);

  const toggleOutcome = (o: Outcome) => {
    markInteracted();
    setActive((prev) => (prev.includes(o) ? prev.filter((x) => x !== o) : [...prev, o]));
  };

  const focus = shillerById(focusId);
  const focusTokens = graphTokens.filter((t) => t.shilledBy.includes(focusId));
  const isDimmed = (o: Outcome) => active.length > 0 && !active.includes(o);

  const ariaLabel =
    `Network of ${shillers.length} shillers and ${graphTokens.length} tokens. ` +
    `Token outcomes: win ${counts.success}, rug ${counts.rug}, dead ${counts.dead}, neutral ${counts.neutral}. ` +
    `Selected: ${focus.handle}, ${focus.smarts} smart followers, ${focus.ca} memecoins called, ${focusTokens.length} tokens in the graph, win rate ${pct(focus.hitRate)}, rug rate ${pct(focus.rugRate)}. ` +
    `Details are in the side panel and the shiller list.`;

  return (
    <div
      onPointerEnter={() => engineRef.current?.setPaused(true)}
      onPointerLeave={() => engineRef.current?.setPaused(false)}
      onPointerDown={markInteracted}
      onFocusCapture={() => {
        markInteracted();
        engineRef.current?.setPaused(true);
      }}
      onBlurCapture={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) engineRef.current?.setPaused(false);
      }}
    >
      {/* filter chips = legend */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="mono-label mr-1 text-muted">Token outcome</span>
        {FILTER_OUTCOMES.map((o) => {
          const on = active.includes(o);
          const faded = active.length > 0 && !on;
          return (
            <button
              key={o}
              type="button"
              aria-pressed={on}
              onClick={() => toggleOutcome(o)}
              className={`mono-label inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[0.68rem] transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                on ? "border-fg/70 bg-ink-3 text-fg" : "border-line text-muted hover:border-fg/40 hover:text-fg"
              } ${faded ? "opacity-55" : ""}`}
            >
              <span className="size-2 rounded-full" style={{ background: outcomeColor[o] }} aria-hidden />
              {outcomeLabel[o]}
              <span className="text-muted">{counts[o]}</span>
            </button>
          );
        })}
        {active.length > 0 ? (
          <button
            type="button"
            onClick={() => setActive([])}
            className="mono-label rounded-full px-2 py-1.5 text-[0.68rem] text-accent underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            reset
          </button>
        ) : null}
        <DemoTag className="ml-auto" />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0">
          <div
            className="relative overflow-hidden rounded-2xl border border-line bg-ink-2"
            style={{
              backgroundImage:
                "radial-gradient(ellipse at 50% 45%, rgba(255,79,23,0.07), transparent 62%), radial-gradient(rgba(237,237,235,0.07) 1px, transparent 1px)",
              backgroundSize: "100% 100%, 22px 22px",
            }}
          >
            <canvas
              ref={canvasRef}
              role="img"
              aria-label={ariaLabel}
              className="block h-[380px] w-full sm:h-[470px] lg:h-[580px]"
              style={{ touchAction: "pan-y" }}
            />
            <div className="pointer-events-none absolute left-3 top-3 mono-label flex flex-col gap-1 text-[0.62rem] text-muted sm:left-4 sm:top-4">
              <span>
                <span className="text-fg">{shillers.length}</span> shillers · <span className="text-fg">{graphTokens.length}</span>{" "}
                tokens
              </span>
              <span className="hidden sm:block">
                <span className="text-fg">{cloudCount}</span> history points
              </span>
            </div>
            <div className="pointer-events-none absolute bottom-3 left-3 right-3 mono-label hidden text-[0.62rem] text-muted sm:left-4 sm:block">
              large node — shiller · dots around it — past calls · coloured dot — token · line — “called this token”
            </div>
          </div>

          <div className="mt-3">
            <GraphMetricsLegend />
          </div>

          {/* keyboard / touch friendly shiller list */}
          <div className="mt-4">
            <p className="mono-label mb-2 text-muted" id="graph-shillers-label">
              Shillers
            </p>
            <div role="group" aria-labelledby="graph-shillers-label" className="flex flex-wrap gap-2">
              {shillers.map((s) => {
                const on = s.id === focusId;
                return (
                  <button
                    key={s.id}
                    type="button"
                    aria-pressed={on}
                    onClick={() => selectShiller(s.id)}
                    className={`mono-label rounded-full border px-3 py-1.5 text-[0.7rem] normal-case tracking-normal transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                      on
                        ? "border-accent bg-accent text-ink"
                        : "border-line text-muted hover:border-fg/40 hover:text-fg"
                    }`}
                  >
                    {s.handle}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <ShillerPanel
          shiller={focus}
          tokens={focusTokens}
          isDimmed={isDimmed}
          live={interacted}
          avgHit={avgHit}
          avgRug={avgRug}
        />
      </div>
    </div>
  );
}

type PanelProps = {
  shiller: Shiller;
  tokens: typeof graphTokens;
  isDimmed: (o: Outcome) => boolean;
  /** announce focus changes only after the user has interacted */
  live: boolean;
  avgHit: number;
  avgRug: number;
};

function ShillerPanel({ shiller, tokens, isDimmed, live, avgHit, avgRug }: PanelProps) {
  return (
    <aside
      aria-label="Shiller profile"
      className="flex flex-col rounded-2xl border border-line bg-ink-2 p-5 sm:p-6"
    >
      <div aria-live={live ? "polite" : "off"}>
        <p className="mono-label text-muted">In focus</p>
        <div className="mt-3 flex items-center gap-3">
          <Avatar username={shiller.handle} size={48} remote={false} />
          <h3 className="min-w-0 break-all font-mono text-2xl font-medium tracking-tight text-fg">{shiller.handle}</h3>
        </div>
        <p className="mono-label mt-2 text-muted">
          <span className="text-fg">{formatFollowers(shiller.followers)}</span> followers ·{" "}
          <span className="text-fg">{shiller.calls}</span> calls
        </p>
        <dl className="mt-4 grid grid-cols-2 gap-3 font-mono">
          <div>
            <dt className="mono-label text-muted">SMARTS</dt>
            <dd className="mt-1 text-lg text-fg"><span className="text-accent" aria-hidden="true">✦ </span>{shiller.smarts}</dd>
          </div>
          <div>
            <dt className="mono-label text-muted">CA</dt>
            <dd className="mt-1 text-lg" style={{ color: caTierColor[caTier(shiller.ca)] }}>{shiller.ca}</dd>
          </div>
        </dl>
      </div>

      <div className="mt-6 flex flex-col gap-4">
        <Metric label="Win rate" value={pct(shiller.hitRate)} ratio={shiller.hitRate} avg={avgHit} color="var(--color-win)" />
        <Metric label="Rug rate" value={pct(shiller.rugRate)} ratio={shiller.rugRate} avg={avgRug} color="var(--color-rug)" />
        <Metric
          label="Median minute"
          value={`${shiller.medianMinute} min`}
          ratio={Math.min(1, shiller.medianMinute / 15)}
          color="var(--color-accent)"
          note="after token creation"
        />
      </div>

      <div className="mt-6 border-t border-line pt-5">
        <p className="mono-label mb-3 flex items-center justify-between text-muted">
          <span>Tokens in graph</span>
          <span className="text-fg">{tokens.length}</span>
        </p>
        <ul className="grid grid-cols-1 gap-x-4 gap-y-1.5 min-[420px]:grid-cols-2 lg:grid-cols-1">
          {tokens.map((t) => (
            <li
              key={t.id}
              className={`flex items-center gap-2 font-mono text-[0.78rem] transition-opacity ${
                isDimmed(t.outcome) ? "opacity-35" : "opacity-100"
              }`}
            >
              <span className="size-2 shrink-0 rounded-full" style={{ background: outcomeColor[t.outcome] }} aria-hidden />
              <span className="truncate text-fg">{t.ticker}</span>
              <span className="ml-auto shrink-0 text-[0.68rem] text-muted">{outcomeLabel[t.outcome]}</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="mt-5 text-xs leading-relaxed text-muted">
        Rates use all of the shiller’s labelled calls; the graph shows only some of the tokens. The tick on each bar is the database average.
      </p>
    </aside>
  );
}

function Metric({
  label,
  value,
  ratio,
  avg,
  color,
  note,
}: {
  label: string;
  value: string;
  ratio: number;
  avg?: number;
  color: string;
  note?: string;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="mono-label text-[0.66rem] text-muted">{label}</span>
        <span className="font-mono text-lg text-fg">{value}</span>
      </div>
      <div className="relative mt-2 h-1.5 rounded-full bg-line" role="presentation">
        <div
          className="h-full rounded-full transition-[width] duration-700 ease-out motion-reduce:transition-none"
          style={{ width: `${Math.max(2, Math.round(ratio * 100))}%`, background: color }}
        />
        {avg !== undefined ? (
          <span
            className="absolute -top-1 h-3.5 w-px bg-fg/70"
            style={{ left: `${Math.round(avg * 100)}%` }}
            aria-hidden
          />
        ) : null}
      </div>
      {note ? <p className="mt-1 text-[0.68rem] text-muted">{note}</p> : null}
    </div>
  );
}
