"use client";

import { demoSignal } from "@/lib/demo";
import { BlockTitle } from "./BlockTitle";
import { useInView } from "./useInView";

export function Risks() {
  const { ref, on } = useInView<HTMLUListElement>(0.4);

  return (
    <div>
      <BlockTitle>Risks</BlockTitle>
      <ul ref={ref} className="space-y-3">
        {demoSignal.risks.map((r, i) => (
          <li
            key={r}
            className={`flex items-start gap-3 text-sm leading-snug text-fg/90 transition-all duration-500 motion-reduce:transition-none ${
              on ? "translate-x-0 opacity-100" : "-translate-x-2 opacity-0"
            }`}
            style={{ transitionDelay: `${i * 180}ms` }}
          >
            <svg
              viewBox="0 0 16 16"
              className="mt-0.5 size-4 shrink-0 text-accent"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M8 1.8 14.6 13.4H1.4L8 1.8Z" />
              <path d="M8 6.4v3.2" strokeLinecap="round" />
              <circle cx="8" cy="11.3" r="0.5" fill="currentColor" />
            </svg>
            <span>{r}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
