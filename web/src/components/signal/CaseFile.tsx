import { demoSignal } from "@/lib/demo";
import { DemoTag } from "@/components/ui/DemoTag";
import { Confidence } from "./Confidence";
import { Inside } from "./Inside";
import { Risks } from "./Risks";
import { Similar } from "./Similar";
import { Timeline } from "./Timeline";

const stats = [
  { label: "Age", value: `${demoSignal.ageMinutes} min` },
  { label: "MCap", value: `${demoSignal.marketCapSol} SOL` },
  { label: "Holders", value: String(demoSignal.holders) },
];

/** The "case file": one signal about a token that is only a few minutes old. */
export function CaseFile() {
  return (
    <article
      aria-label={`Signal card for ${demoSignal.ticker}`}
      className="relative overflow-hidden rounded-2xl border border-line bg-ink-2"
    >
      {/* faint grid */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-40 [background-image:linear-gradient(var(--color-line)_1px,transparent_1px),linear-gradient(90deg,var(--color-line)_1px,transparent_1px)] [background-size:32px_32px] [mask-image:radial-gradient(ellipse_at_top_left,black,transparent_70%)]"
      />

      <div className="relative">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3 sm:px-8">
          <p className="mono-label flex items-center gap-2 text-muted">
            <span className="size-1.5 animate-pulse rounded-full bg-accent motion-reduce:animate-none" aria-hidden />
            Kairos · signal card
          </p>
          <DemoTag />
        </div>

        <header className="flex flex-col gap-6 p-5 sm:p-8 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <h3 className="truncate text-5xl font-extrabold uppercase leading-none tracking-tighter sm:text-7xl">
              {demoSignal.ticker}
            </h3>
            <p className="mt-4 inline-flex max-w-full items-center gap-2 rounded-full border border-line bg-ink-3/80 px-3 py-1.5 font-mono text-xs text-muted">
              <span className="text-[10px] uppercase tracking-wider">CA</span>
              <span className="min-w-0 truncate text-fg">{demoSignal.ca}</span>
              <svg
                viewBox="0 0 16 16"
                className="size-3.5 shrink-0"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.3"
                aria-hidden
              >
                <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
                <path d="M10.5 5.5v-2a1.5 1.5 0 0 0-1.5-1.5H4A1.5 1.5 0 0 0 2.5 3.5V9A1.5 1.5 0 0 0 4 10.5h1.5" />
              </svg>
            </p>
          </div>
          <dl className="grid grid-cols-3 gap-x-5 gap-y-2 sm:gap-x-10">
            {stats.map((s) => (
              <div key={s.label}>
                <dt className="mono-label text-[0.62rem] text-muted">{s.label}</dt>
                <dd className="mt-1 font-mono text-base text-fg sm:text-2xl">{s.value}</dd>
              </div>
            ))}
          </dl>
        </header>

        <div className="grid border-t border-line lg:grid-cols-[1.2fr_1fr]">
          <div className="min-w-0 space-y-10 p-5 sm:p-8">
            <Timeline />
            <Inside />
          </div>
          <div className="min-w-0 space-y-10 border-t border-line p-5 sm:p-8 lg:border-l lg:border-t-0">
            <Similar />
            <Confidence />
            <Risks />
          </div>
        </div>

        <p className="border-t border-line px-5 py-4 font-mono text-[11px] text-muted sm:px-8">
          Not financial advice · demo data
        </p>
      </div>
    </article>
  );
}
