"use client";

import { useEffect, useRef } from "react";
import { shillers } from "@/lib/demo";
import { useReducedMotion } from "@/lib/useReducedMotion";
import { createGraphEngine, type GraphEngine } from "./engine";

/** Uninterrupted article playback; still respects the OS motion preference. */
export function GraphAnimation({ showLegend, labelScale = 1 }: { showLegend: boolean; labelScale?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<GraphEngine | null>(null);
  const reduced = useReducedMotion();
  const reducedRef = useRef(reduced);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const engine = createGraphEngine(canvas, {
      initialFocus: shillers[0].id,
      reduced: reducedRef.current,
      startImmediately: true,
      interactive: false,
      verticalPadding: showLegend ? 100 : undefined,
      labelScale,
      // Video-friendly playback: still camera, longer hold on each shiller, slow highlight fades.
      staticCamera: true,
      cycleMs: 6500,
      focusMs: 650,
      onFocus: () => {},
    });
    engineRef.current = engine;
    return () => {
      engine.destroy();
      engineRef.current = null;
    };
  }, [showLegend, labelScale]);

  useEffect(() => {
    reducedRef.current = reduced;
    engineRef.current?.setReduced(reduced);
  }, [reduced]);

  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label="Illustrative network of seven shillers and their tokens. Large nodes show handles, smart follower counts and distinct memecoin counts. Focus cycles automatically between shillers."
      className="block h-full w-full"
    />
  );
}
