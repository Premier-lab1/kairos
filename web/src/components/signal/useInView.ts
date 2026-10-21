"use client";

import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "@/lib/useReducedMotion";

/** Becomes true once the element is in the viewport (stays true). Always true under reduced motion. */
export function useInView<T extends HTMLElement>(threshold = 0.3) {
  const ref = useRef<T>(null);
  const reduced = useReducedMotion();
  const [seen, setSeen] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || reduced) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setSeen(true);
          io.disconnect();
        }
      },
      { threshold, rootMargin: "0px 0px -8% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [reduced, threshold]);

  return { ref, on: seen || reduced, reduced };
}
