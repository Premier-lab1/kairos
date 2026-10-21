"use client";

import { demoSignal, outcomeColor, outcomeLabel, tokenById } from "@/lib/demo";
import { BlockTitle } from "./BlockTitle";
import { pct } from "./format";
import { useInView } from "./useInView";

export function Similar() {
  const { ref, on } = useInView<HTMLUListElement>(0.25);

  return (
    <div>
      <BlockTitle>Looks like</BlockTitle>
      <ul ref={ref} className="border-b border-line">
        {demoSignal.similar.map((c, i) => {
          const t = tokenById(c.tokenId);
          const color = outcomeColor[t.outcome];
          const d = 150 + i * 200;
          return (
            <li
              key={c.tokenId}
              className={`border-t border-line py-3.5 transition-all duration-500 motion-reduce:transition-none ${
                on ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0"
              }`}
              style={{ transitionDelay: `${d - 100}ms` }}
            >
              <div className="flex items-center justify-between gap-3">
                <span className="min-w-0 truncate font-mono text-sm text-fg">{t.ticker}</span>
                <span
                  className="mono-label inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[0.62rem]"
                  style={{ color, borderColor: `color-mix(in srgb, ${color} 50%, transparent)` }}
                >
                  <span className="size-1.5 rounded-full" style={{ background: color }} aria-hidden />
                  {outcomeLabel[t.outcome]}
                </span>
              </div>
              <div className="mt-2.5 flex items-center gap-3">
                <div className="h-[3px] flex-1 overflow-hidden bg-line">
                  <div
                    className="h-full origin-left bg-fg/70 transition-transform duration-[900ms] ease-out motion-reduce:transition-none"
                    style={{ transform: on ? `scaleX(${c.similarity})` : "scaleX(0)", transitionDelay: `${d}ms` }}
                  />
                </div>
                <span className="w-24 shrink-0 text-right font-mono text-[10px] text-muted">
                  similarity <span className="text-fg">{pct(c.similarity)}%</span>
                </span>
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        Compared by which shillers are in, their entry minute and the number of mentions.
      </p>
    </div>
  );
}
