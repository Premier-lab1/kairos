"use client";

import { Avatar } from "@/components/ui/Avatar";
import { caTier, caTierColor, demoSignal, shillerById } from "@/lib/demo";
import { BlockTitle } from "./BlockTitle";
import { fmtFollowers, pct, pluralCalls } from "./format";
import { useInView } from "./useInView";

function Bar({
  label,
  value,
  color,
  on,
  delay,
}: {
  label: string;
  value: number;
  color: string;
  on: boolean;
  delay: number;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between font-mono text-[10px] text-muted">
        <span>{label}</span>
        <span className="text-fg">{pct(value)}%</span>
      </div>
      <div className="h-[3px] overflow-hidden bg-line">
        <div
          className={`h-full origin-left transition-transform duration-[900ms] ease-out motion-reduce:transition-none ${color}`}
          style={{ transform: on ? `scaleX(${value})` : "scaleX(0)", transitionDelay: `${delay}ms` }}
        />
      </div>
    </div>
  );
}

export function Inside() {
  const { ref, on } = useInView<HTMLUListElement>(0.25);
  const rows = [...demoSignal.inside].sort((a, b) => a.minute - b.minute);

  return (
    <div>
      <BlockTitle aside={`${rows.length} shillers from the database`}>Who is already in</BlockTitle>
      <ul ref={ref} className="border-b border-line">
        {rows.map((r, i) => {
          const s = shillerById(r.shillerId);
          const d = 200 + i * 220;
          return (
            <li
              key={r.shillerId}
              className={`border-t border-line py-4 transition-all duration-500 motion-reduce:transition-none ${
                on ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0"
              }`}
              style={{ transitionDelay: `${d - 150}ms` }}
            >
              <div className="flex items-center gap-3">
                <Avatar username={s.handle} size={36} remote={false} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="min-w-0 truncate font-mono text-sm text-fg">{s.handle}</span>
                    <span className="shrink-0 font-mono text-[11px] text-accent">at minute {r.minute}</span>
                  </div>
                  <p className="mt-1 truncate font-mono text-[11px] text-muted">
                    {fmtFollowers(s.followers)} followers · {s.calls} {pluralCalls(s.calls)}
                  </p>
                </div>
              </div>
              <p className="mt-2.5 flex items-center gap-4 font-mono text-[11px] text-muted">
                <span title="SMARTS: smart followers"><span className="text-accent" aria-hidden="true">✦</span> SMARTS <span className="text-fg">{s.smarts}</span></span>
                <span title="CA: memecoins called. Green below 10, yellow 10–19, red 20 and above">CA <span style={{ color: caTierColor[caTier(s.ca)] }}>{s.ca}</span></span>
              </p>
              <div className="mt-3 grid grid-cols-2 gap-4">
                <Bar label="win" value={s.hitRate} color="bg-win" on={on} delay={d} />
                <Bar label="rug" value={s.rugRate} color="bg-rug" on={on} delay={d + 120} />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
