import { Button } from "@/components/ui/Button";
import { DemoTag } from "@/components/ui/DemoTag";
import { HeroScene } from "@/components/hero/HeroScene";
import { HeroStill } from "@/components/hero/HeroStill";
import { demoSignal, outcomeLabel, shillers, tokenById } from "@/lib/demo";

const RING_R = 7;
const RING_C = 2 * Math.PI * RING_R;

/**
 * Hero: full-viewport dark "cluster-signal" scene (three.js, lazy) behind server-rendered
 * copy, a bottom HUD strip with demo metrics. The fixed 64px header overlays the top.
 */
export function Hero() {
  const lastInside = demoSignal.inside[demoSignal.inside.length - 1];
  const minute = String(lastInside.minute).padStart(2, "0");
  const confidence = String(demoSignal.confidence).padStart(2, "0");
  const similarWins = demoSignal.similar.filter((s) => tokenById(s.tokenId).outcome === "success").length;

  return (
    <section
      id="hero"
      className="relative isolate flex min-h-[100svh] flex-col overflow-hidden bg-ink text-fg"
      aria-labelledby="hero-title"
    >
      {/*
        Stacking: the scene and the scrims sit at z-[-1] inside the section's own stacking
        context (`isolate`), so all in-flow copy paints above them without extra wrappers.
      */}
      <div className="mx-auto flex w-full max-w-[1320px] flex-1 flex-col px-4 pb-14 pt-24 sm:px-8 sm:pb-16 sm:pt-28 lg:pt-32">
        <div className="flex flex-col md:flex-1 md:justify-center">
          <p className="mono-label mb-5 flex items-center gap-3 text-muted sm:mb-7">
            <span className="relative flex size-2">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent/60 motion-reduce:hidden" />
              <span className="relative inline-flex size-2 rounded-full bg-accent" />
            </span>
            Solana · Pump.fun
          </p>

          <h1
            id="hero-title"
            className="headline max-w-[12ch]"
            style={{ fontSize: "clamp(2.5rem, 8vw, 7.25rem)" }}
          >
            Early signal <em>while the trench forms</em>
          </h1>

          <p className="mt-6 max-w-[34rem] text-base text-fg/80 sm:mt-8 sm:text-lg md:text-xl">
            Kairos matches a new token against shiller history: who is already in, at which minute, and how
            similar cases ended.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3 sm:mt-10">
            <Button href="/app">Open dashboard</Button>
            <Button href="#how" variant="ghost">
              How it works
              <span aria-hidden className="text-muted">
                ↓
              </span>
            </Button>
          </div>
        </div>

        {/*
          Mobile: a flexible spacer between the copy and the HUD; the scene layer is centred on it
          (and sized to the viewport), so the token always lands in the free space, whatever the
          text height. Desktop (md+): the spacer is static and the layer covers the whole section.
        */}
        <div className="relative min-h-[12.5rem] flex-1 md:static md:min-h-0 md:flex-none">
          <HeroScene still={<HeroStill />} />
        </div>

        {/* HUD strip */}
        <div className="mt-6 md:mt-20">
          <div className="mb-3 flex items-center justify-between gap-4 md:hidden">
            <span className="mono-label flex items-center gap-2 text-[0.62rem] text-fg">
              <span className="size-1.5 rounded-full bg-accent" aria-hidden />
              {demoSignal.ticker}
            </span>
            <DemoTag className="text-muted" label="Demo" />
          </div>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 border-t border-line/80 pt-4 font-mono text-sm sm:grid-cols-4 md:flex md:items-end md:gap-10 md:pt-5">
            <div className="hidden md:block">
              <dt className="mono-label mb-1.5 text-[0.62rem] text-muted">Token</dt>
              <dd className="flex items-center gap-2 text-fg">
                <span className="size-1.5 rounded-full bg-accent" aria-hidden />
                {demoSignal.ticker}
                <span className="text-muted">{demoSignal.ca}</span>
              </dd>
            </div>
            <div>
              <dt className="mono-label mb-1.5 text-[0.62rem] text-muted">Minute</dt>
              <dd className="text-fg">
                <span className="text-lg leading-none tabular-nums" data-hero-live="minute">
                  {minute}
                </span>
                <span className="ml-1 text-muted">/ 60</span>
              </dd>
            </div>
            <div>
              <dt className="mono-label mb-1.5 text-[0.62rem] text-muted">Shillers in</dt>
              <dd className="text-fg">
                <span className="text-lg leading-none tabular-nums" data-hero-live="inside">
                  {demoSignal.inside.length}
                </span>
                <span className="ml-1 text-muted">/ {shillers.length} tracked</span>
              </dd>
            </div>
            <div>
              <dt className="mono-label mb-1.5 text-[0.62rem] text-muted">Confidence</dt>
              <dd className="flex items-center gap-2 text-fg">
                <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden className="-rotate-90 shrink-0">
                  <circle cx="9" cy="9" r={RING_R} fill="none" stroke="currentColor" strokeOpacity="0.18" strokeWidth="1.5" />
                  <circle
                    cx="9"
                    cy="9"
                    r={RING_R}
                    fill="none"
                    stroke="var(--color-accent)"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeDasharray={RING_C.toFixed(2)}
                    strokeDashoffset={(RING_C * (1 - demoSignal.confidence / 100)).toFixed(2)}
                    data-hero-live="ring"
                    className="transition-[stroke-dashoffset] duration-200"
                  />
                </svg>
                <span className="text-lg leading-none tabular-nums" data-hero-live="confidence">
                  {confidence}
                </span>
                <span className="text-muted">/ 100</span>
              </dd>
            </div>
            <div>
              <dt className="mono-label mb-1.5 text-[0.62rem] text-muted">Similar cases</dt>
              <dd className="text-fg">
                <span className="text-lg leading-none tabular-nums">{demoSignal.similar.length}</span>
                <span className="ml-1 text-muted">
                  · <span className="text-win">{similarWins}</span> {outcomeLabel.success}
                </span>
              </dd>
            </div>
            <div className="ml-auto hidden md:block">
              <DemoTag className="text-muted" />
            </div>
          </dl>
        </div>
      </div>

      {/* scrims (after the scene in DOM order, same z-[-1] layer → painted over it, under the copy):
          top on mobile behind the copy, left on desktop, bottom fade into the next section */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 -z-[1] h-[58%] bg-linear-to-b from-ink via-ink/75 to-transparent md:hidden"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-y-0 left-0 -z-[1] hidden w-[60%] bg-linear-to-r from-ink/85 via-ink/35 to-transparent md:block"
      />
      <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 -z-[1] h-40 bg-linear-to-t from-ink to-transparent" />
    </section>
  );
}
