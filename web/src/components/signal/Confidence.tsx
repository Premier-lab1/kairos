"use client";

import { useEffect, useState } from "react";
import { demoSignal } from "@/lib/demo";
import { BlockTitle } from "./BlockTitle";
import { useInView } from "./useInView";

const SLIDE_MS = 1400;
const SEGMENTS = [
  { label: "low", from: 0, to: 40, cls: "bg-rug/50" },
  { label: "medium", from: 40, to: 70, cls: "bg-flat" },
  { label: "high", from: 70, to: 100, cls: "bg-win/60" },
];

export function Confidence() {
  const { ref, on, reduced } = useInView<HTMLDivElement>(0.4);
  const { confidence, verdict } = demoSignal;
  const [value, setValue] = useState(0);

  // count the number up in step with the marker
  useEffect(() => {
    if (!on || reduced) return;
    let raf = 0;
    const t0 = performance.now() + 150;
    const tick = (now: number) => {
      const k = Math.min(1, Math.max(0, (now - t0) / SLIDE_MS));
      setValue(Math.round(confidence * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [on, reduced, confidence]);

  const shown = reduced ? confidence : value;

  return (
    <div ref={ref}>
      <BlockTitle aside="rules + stats">Confidence</BlockTitle>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-5">
        <p className="font-mono leading-none" aria-label={`Confidence ${confidence} out of 100`}>
          <span className="text-6xl font-light tabular-nums tracking-tight sm:text-7xl">{shown}</span>
          <span className="ml-1 text-base text-muted">/100</span>
        </p>
        <span
          className={`inline-block -rotate-6 rounded-md border-2 border-accent px-3 py-1.5 font-mono text-[0.78rem] font-bold uppercase leading-tight tracking-wider text-accent outline outline-1 outline-offset-2 outline-accent/40 transition duration-500 ease-out motion-reduce:transition-none ${
            on ? "scale-100 opacity-100" : "scale-[1.7] opacity-0"
          }`}
          style={{ transitionDelay: on && !reduced ? `${SLIDE_MS + 250}ms` : "0ms" }}
        >
          {verdict}
        </span>
      </div>

      <div className="relative mb-6 mt-9 pt-7">
        {/* marker */}
        <div
          aria-hidden
          className="absolute top-0 z-10 flex -translate-x-1/2 flex-col items-center transition-[left] ease-out motion-reduce:transition-none"
          style={{
            left: on ? `${confidence}%` : "0%",
            transitionDuration: `${SLIDE_MS}ms`,
            transitionDelay: "150ms",
          }}
        >
          <span className="rounded bg-fg px-1.5 py-0.5 font-mono text-[10px] font-semibold leading-none text-ink">
            {shown}
          </span>
          <span className="h-4 w-px bg-fg" />
        </div>
        {/* segments */}
        <div className="flex gap-[2px]" role="img" aria-label="Scale: low below 40, medium 40–70, high above 70">
          {SEGMENTS.map((s) => (
            <div key={s.label} style={{ width: `${s.to - s.from}%` }}>
              <div className={`h-2 ${s.cls}`} />
              <p className="mt-2 font-mono text-[10px] text-muted">{s.label}</p>
            </div>
          ))}
        </div>
        {/* boundary numbers */}
        <div aria-hidden className="relative mt-1 h-3 font-mono text-[10px] text-muted/70">
          <span className="absolute left-0">0</span>
          <span className="absolute -translate-x-1/2" style={{ left: "40%" }}>
            40
          </span>
          <span className="absolute -translate-x-1/2" style={{ left: "70%" }}>
            70
          </span>
          <span className="absolute right-0">100</span>
        </div>
      </div>
    </div>
  );
}
