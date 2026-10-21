"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useReducedMotion } from "@/lib/useReducedMotion";
import { demoSignal } from "@/lib/demo";
import { DESKTOP_QUERY, NODES, SCENE_LABEL } from "./layout";
import { createHeroScene, type HudState, type SceneHandle } from "./scene";

const RING_C = 2 * Math.PI * 7; // r=7 ring in the HUD

/**
 * Full-bleed layer behind the hero text. Renders the server still (`still`) immediately;
 * on the client it lazily imports three.js, builds the scene and fades the canvas in over
 * the still. Falls back to the still when WebGL is missing or reduced motion is requested.
 */
export function HeroScene({ still }: { still: ReactNode }) {
  const reduced = useReducedMotion();
  const rootRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const labelRefs = useRef<(HTMLElement | null)[]>([]);
  const tokenLabelRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (reduced) return;
    const root = rootRef.current;
    const canvas = canvasRef.current;
    if (!root || !canvas) return;
    const section = (root.closest("section") as HTMLElement | null) ?? root;
    // cheap WebGL probe before paying for the three.js chunk
    const probe = document.createElement("canvas");
    const gl = probe.getContext("webgl2") || probe.getContext("webgl");
    if (!gl) return;

    let cancelled = false;
    let handle: SceneHandle | null = null;
    let inView = true;
    const cleanups: (() => void)[] = [];

    // HUD live values (server-rendered defaults stay for no-JS / reduced motion)
    const live = {
      minute: section.querySelectorAll<HTMLElement>('[data-hero-live="minute"]'),
      inside: section.querySelectorAll<HTMLElement>('[data-hero-live="inside"]'),
      confidence: section.querySelectorAll<HTMLElement>('[data-hero-live="confidence"]'),
      ring: section.querySelectorAll<SVGCircleElement>('[data-hero-live="ring"]'),
    };
    const pad = (n: number) => String(n).padStart(2, "0");
    const onHud = (s: HudState) => {
      live.minute.forEach((el) => (el.textContent = pad(s.minute)));
      live.inside.forEach((el) => (el.textContent = String(s.inside)));
      live.confidence.forEach((el) => (el.textContent = pad(s.confidence)));
      live.ring.forEach((el) => el.style.setProperty("stroke-dashoffset", (RING_C * (1 - s.confidence / 100)).toFixed(2)));
    };
    // final state shown by the SSR still — restored whenever we fall back to it
    const finalHud: HudState = {
      minute: demoSignal.inside[demoSignal.inside.length - 1].minute,
      inside: demoSignal.inside.length,
      confidence: demoSignal.confidence,
    };

    /** Back to the still: dispose the scene (idempotent) and put the HUD in its final state. */
    const teardown = () => {
      handle?.dispose();
      handle = null;
      onHud(finalHud);
      setReady(false);
    };

    const syncRun = () => {
      if (!handle) return;
      if (inView && document.visibilityState === "visible") handle.start();
      else handle.stop();
    };

    import("three")
      .then((THREE) => {
        if (cancelled) return;
        let created: SceneHandle;
        try {
          created = createSceneSafely(THREE);
        } catch {
          return; // keep the still
        }
        handle = created;
        const rect = root.getBoundingClientRect();
        handle.resize(rect.width, rect.height);
        syncRun();
      })
      .catch(() => {
        /* chunk failed to load — the still stays */
      });

    function createSceneSafely(THREE: typeof import("three")) {
      return createHeroScene(THREE, {
        canvas: canvas!,
        labels: labelRefs.current,
        tokenLabel: tokenLabelRef.current,
        small: !window.matchMedia(DESKTOP_QUERY).matches,
        onHud,
        onReady: () => {
          if (!cancelled) setReady(true);
        },
        onContextLost: () => {
          // the scene already disposed itself; no restore attempt for this page view
          if (!cancelled) teardown();
        },
      });
    }

    // size
    const ro = new ResizeObserver((entries) => {
      const e = entries[0];
      if (!e || !handle) return;
      handle.resize(e.contentRect.width, e.contentRect.height);
    });
    ro.observe(root);
    cleanups.push(() => ro.disconnect());

    // visibility: pause the loop offscreen or in a hidden tab
    const io = new IntersectionObserver(
      (entries) => {
        inView = entries.some((e) => e.isIntersecting);
        syncRun();
      },
      { threshold: 0 },
    );
    io.observe(root);
    cleanups.push(() => io.disconnect());
    document.addEventListener("visibilitychange", syncRun);
    cleanups.push(() => document.removeEventListener("visibilitychange", syncRun));

    // scroll progress: read once per frame inside the loop would need layout; a passive
    // scroll listener + rAF coalescing keeps it to one read per frame
    let scrollRaf = 0;
    const readScroll = () => {
      scrollRaf = 0;
      if (!handle) return;
      const r = section.getBoundingClientRect();
      handle.setScroll(r.height > 0 ? -r.top / r.height : 0);
    };
    const onScroll = () => {
      if (!scrollRaf) scrollRaf = requestAnimationFrame(readScroll);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    cleanups.push(() => {
      window.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(scrollRaf);
    });
    onScroll();

    // pointer: hover parallax (mouse) + light drag-to-rotate; never blocks page scrolling
    let dragging = false;
    let lastX = 0;
    const onMove = (e: PointerEvent) => {
      if (!handle) return;
      if (dragging) {
        handle.addDrag((e.clientX - lastX) * 0.004);
        lastX = e.clientX;
        return;
      }
      if (e.pointerType !== "mouse") return;
      const r = section.getBoundingClientRect();
      const nx = ((e.clientX - r.left) / r.width) * 2 - 1;
      const ny = ((e.clientY - r.top) / r.height) * 2 - 1;
      handle.setPointer(Math.max(-1, Math.min(1, nx)), Math.max(-1, Math.min(1, ny)));
    };
    const onDown = (e: PointerEvent) => {
      // don't hijack links, buttons or text selection
      if ((e.target as Element | null)?.closest("a, button, input, h1, p, dl")) return;
      dragging = true;
      lastX = e.clientX;
    };
    const onUp = () => {
      dragging = false;
    };
    const onLeave = () => handle?.setPointer(0, 0);
    section.addEventListener("pointermove", onMove, { passive: true });
    section.addEventListener("pointerdown", onDown, { passive: true });
    section.addEventListener("pointerleave", onLeave);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    cleanups.push(() => {
      section.removeEventListener("pointermove", onMove);
      section.removeEventListener("pointerdown", onDown);
      section.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    });

    return () => {
      // reduced motion switched on, or unmount: scene away, HUD back to the still's values
      cancelled = true;
      for (const c of cleanups) c();
      teardown();
    };
  }, [reduced]);

  const showCanvas = ready && !reduced;
  const transform =
    "translate(-50%, -50%) translate(calc(var(--hx) * var(--hs) * 100%), calc(var(--hy) * var(--hs) * 100%)) scale(var(--hs))";

  return (
    <div
      ref={rootRef}
      // mobile: viewport-sized layer centred on the spacer it lives in; md+: covers the section
      className="pointer-events-none absolute left-1/2 top-1/2 -z-[1] h-[100svh] w-screen -translate-x-1/2 -translate-y-1/2 overflow-hidden md:inset-0 md:h-auto md:w-auto md:translate-x-0 md:translate-y-0"
      role="img"
      aria-label={SCENE_LABEL}
    >
      {/* SSR still — the CSS vars below MUST equal layout.ts PRESETS (mobile / md:desktop);
          Tailwind only picks up literal class names, hence no interpolation */}
      <div
        aria-hidden="true"
        className="absolute left-1/2 top-1/2 transition-opacity duration-[1200ms] ease-out [--hx:0] [--hy:0] [--hs:0.5] md:[--hx:0.2] md:[--hy:-0.02] md:[--hs:1]"
        style={{
          width: "calc(100% / var(--hs))",
          height: "calc(100% / var(--hs))",
          transform,
          opacity: showCanvas ? 0 : 1,
        }}
      >
        {still}
      </div>
      {/* WebGL layer */}
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        className="absolute inset-0 h-full w-full transition-opacity duration-[1200ms] ease-out"
        style={{ opacity: showCanvas ? 1 : 0 }}
      />
      {/* projected HTML labels (positions written by the scene each frame) */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 font-mono text-[0.66rem] leading-none tracking-[0.04em] transition-opacity duration-[1200ms]"
        style={{ opacity: showCanvas ? 1 : 0 }}
      >
        {NODES.map((n, i) => (
          <div
            key={n.shillerId}
            ref={(el) => {
              labelRefs.current[i] = el;
            }}
            className={`absolute left-0 top-0 whitespace-nowrap will-change-transform ${n.inside ? "text-fg" : "hidden text-fg/50 md:block"}`}
            style={{ opacity: 0 }}
          >
            {n.inside ? (
              <>
                <span className="mr-1.5 inline-block h-px w-2 bg-fg/50 align-middle" />
                {n.handle} · {n.minute} min
              </>
            ) : (
              n.handle
            )}
          </div>
        ))}
        <div
          ref={tokenLabelRef}
          className="absolute left-0 top-0 whitespace-nowrap text-accent will-change-transform"
          style={{ opacity: 0 }}
        >
          {demoSignal.ticker} · {demoSignal.ca}
        </div>
      </div>
    </div>
  );
}
