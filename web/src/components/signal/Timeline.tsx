"use client";

import { demoSignal, shillerById } from "@/lib/demo";
import { BlockTitle } from "./BlockTitle";
import { useInView } from "./useInView";

const AXIS = 44; // px, y of the minute axis inside the plot
const MINUTES = 10;
const ticks = Array.from({ length: MINUTES + 1 }, (_, i) => i);
const x = (m: number) => `${(m / MINUTES) * 100}%`;
const AXIS_MS = 900;

export function Timeline() {
  const { ref, on } = useInView<HTMLDivElement>(0.4);
  const { ageMinutes, inside } = demoSignal;
  const marks = [...inside].sort((a, b) => a.minute - b.minute);
  const summary = marks.map((m) => `${shillerById(m.shillerId).handle} at minute ${m.minute}`).join(", ");

  return (
    <div>
      <BlockTitle aside="minutes since token creation">Timeline</BlockTitle>
      <div
        ref={ref}
        role="img"
        aria-label={`Timeline 0–10 minutes. In: ${summary}. Now: minute ${ageMinutes}.`}
        className="relative mx-3 h-[126px]"
      >
        {/* minute grid + labels */}
        {ticks.map((m) => (
          <span key={m}>
            <span aria-hidden className="absolute top-0 h-[104px] w-px bg-line/70" style={{ left: x(m) }} />
            <span
              aria-hidden
              className="absolute top-[108px] -translate-x-1/2 font-mono text-[10px] text-muted"
              style={{ left: x(m) }}
            >
              {m}
            </span>
          </span>
        ))}

        {/* axis: base, then the part already lived */}
        <span aria-hidden className="absolute left-0 right-0 h-px bg-fg/25" style={{ top: AXIS }} />
        <span
          aria-hidden
          className="absolute left-0 h-[3px] origin-left bg-accent shadow-[0_0_10px_var(--color-accent)] transition-transform ease-out motion-reduce:transition-none"
          style={{
            top: AXIS - 1,
            width: x(ageMinutes),
            transform: on ? "scaleX(1)" : "scaleX(0)",
            transitionDuration: `${AXIS_MS}ms`,
          }}
        />

        {/* shiller marks */}
        {marks.map((m, i) => {
          const s = shillerById(m.shillerId);
          const top = i % 2 === 0;
          const delay = 250 + (m.minute / ageMinutes) * (AXIS_MS - 150);
          return (
            <span
              key={m.shillerId}
              aria-hidden
              className={`transition-opacity duration-500 motion-reduce:transition-none ${on ? "opacity-100" : "opacity-0"}`}
              style={{ transitionDelay: `${delay}ms` }}
            >
              <span
                className="absolute z-10 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent ring-4 ring-accent/25"
                style={{ left: x(m.minute), top: AXIS }}
              />
              <span
                className="absolute flex h-9 flex-col justify-center border-r border-accent/60 pr-2 text-right"
                style={{
                  right: `calc(100% - ${x(m.minute)})`,
                  top: top ? AXIS - 36 : AXIS,
                  maxWidth: `calc(${x(m.minute)} + 0.75rem)`,
                }}
              >
                <span className="block min-w-0 truncate font-mono text-[10px] leading-4 text-fg sm:text-[11px]">
                  {s.handle}
                </span>
                <span className="font-mono text-[10px] leading-4 text-muted">min {m.minute}</span>
              </span>
            </span>
          );
        })}

        {/* now marker */}
        <span
          aria-hidden
          className={`transition-opacity duration-500 motion-reduce:transition-none ${on ? "opacity-100" : "opacity-0"}`}
          style={{ transitionDelay: `${AXIS_MS}ms` }}
        >
          <span
            className="absolute w-px border-l border-dashed border-accent"
            style={{ left: x(ageMinutes), top: AXIS, height: 36 }}
          />
          <span
            className="mono-label absolute -translate-x-1/2 rounded-full bg-accent px-2 py-0.5 text-[9px] font-semibold text-ink"
            style={{ left: x(ageMinutes), top: 80 }}
          >
            now
          </span>
        </span>
      </div>
    </div>
  );
}
