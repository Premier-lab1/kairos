"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Reveal } from "@/components/ui/Reveal";
import { useReducedMotion } from "@/lib/useReducedMotion";
import { steps } from "./steps";

const clamp = (v: number) => Math.min(1, Math.max(0, v));

/**
 * 5-step pipeline. The connecting line fills with scroll (vertical on mobile, horizontal from lg).
 * Each step owns one segment (from its dot to the next dot); `--f` (0..1) is the fill of that segment.
 */
export function Pipeline() {
  const reduced = useReducedMotion();
  const rootRef = useRef<HTMLOListElement>(null);
  const segRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const dotRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const [activeCount, setActiveCount] = useState(0);

  useEffect(() => {
    if (reduced) return;
    const root = rootRef.current;
    if (!root) return;
    const mq = window.matchMedia("(min-width: 1024px)");
    let raf = 0;

    const update = () => {
      raf = 0;
      const vh = window.innerHeight;
      const horizontal = mq.matches;
      const rootRect = root.getBoundingClientRect();
      // virtual "front" of the line: viewport y (vertical) or x inside the root (horizontal)
      const trigger = vh * 0.72;
      const horizontalFront = clamp((vh * 0.82 - rootRect.top) / (vh * 0.4)) * rootRect.width * 0.8;

      segRefs.current.forEach((seg) => {
        if (!seg) return;
        const r = seg.getBoundingClientRect();
        const f = horizontal
          ? clamp((horizontalFront - (r.left - rootRect.left)) / r.width)
          : clamp((trigger - r.top) / r.height);
        seg.style.setProperty("--f", f.toFixed(3));
      });

      let count = 0;
      dotRefs.current.forEach((dot) => {
        if (!dot) return;
        const r = dot.getBoundingClientRect();
        const reached = horizontal
          ? r.left + r.width / 2 - rootRect.left <= horizontalFront + 1
          : r.top + r.height / 2 <= trigger;
        if (reached) count += 1;
      });
      setActiveCount(count);
    };

    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [reduced]);

  const reached = reduced ? steps.length : activeCount;

  return (
    <ol ref={rootRef} className="relative grid grid-cols-1 lg:grid-cols-5">
      {steps.map((step, i) => {
        const active = i < reached;
        const last = i === steps.length - 1;
        return (
          <li key={step.title} className="relative pb-10 pl-14 last:pb-0 lg:pb-0 lg:pl-0 lg:pr-6 lg:pt-12">
            {/* node */}
            <span
              ref={(el) => {
                dotRefs.current[i] = el;
              }}
              aria-hidden
              className={`absolute left-0 top-0 grid size-7 place-items-center rounded-full border bg-paper transition-colors duration-500 ${
                active ? "border-accent" : "border-ink/25"
              }`}
            >
              <span
                className={`size-2 rounded-full transition-all duration-500 ${
                  active ? "bg-accent shadow-[0_0_12px_2px_var(--color-accent)]" : "bg-ink/20"
                }`}
              />
            </span>

            {/* segment to the next node */}
            {last ? null : (
              <span
                ref={(el) => {
                  segRefs.current[i] = el;
                }}
                aria-hidden
                style={reduced ? ({ "--f": 1 } as CSSProperties) : undefined}
                className="absolute bottom-0 left-[13px] top-7 w-px bg-ink/15 lg:bottom-auto lg:left-7 lg:right-0 lg:top-[13px] lg:h-px lg:w-auto"
              >
                <span className="absolute inset-0 origin-top bg-accent [transform:scaleY(var(--f,0))] lg:origin-left lg:[transform:scaleX(var(--f,0))]" />
              </span>
            )}

            <Reveal delay={i * 120}>
              <p
                className={`font-mono text-[3.25rem] font-light leading-none tracking-tight transition-colors duration-500 sm:text-6xl ${
                  active ? "text-ink" : "text-ink/20"
                }`}
              >
                {String(i + 1).padStart(2, "0")}
              </p>
              <h3 className="mt-4 text-xl font-extrabold uppercase leading-none tracking-tight">{step.title}</h3>
              <p className="mt-3 max-w-[34ch] text-sm leading-relaxed text-ink/65">{step.text}</p>
              <p
                className={`mono-label mt-4 inline-flex rounded-full border px-3 py-1 text-[0.65rem] ${
                  last ? "border-ink bg-ink text-paper" : "border-ink/25 text-ink/70"
                }`}
              >
                {step.source}
              </p>
            </Reveal>
          </li>
        );
      })}
    </ol>
  );
}
