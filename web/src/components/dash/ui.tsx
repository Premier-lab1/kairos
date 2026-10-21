import type { ReactNode } from "react";

/** Small building blocks for dashboard pages. Server components, no client JS. */

export function PageTitle({ title, sub, right }: { title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4 pt-6">
      <div className="min-w-0">
        <h1 className="text-3xl font-extrabold uppercase tracking-tight sm:text-4xl">{title}</h1>
        {sub ? <p className="mt-2 max-w-[70ch] text-sm text-muted">{sub}</p> : null}
      </div>
      {right}
    </div>
  );
}

export function Card({ title, aside, children, className = "" }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-2xl border border-line bg-ink-2 p-4 sm:p-5 ${className}`}>
      {title ? (
        <header className="mb-4 flex items-center justify-between gap-3">
          <h2 className="mono-label flex items-center gap-2 text-muted">
            <span className="size-1.5 bg-accent" aria-hidden />
            {title}
          </h2>
          {aside ? <span className="font-mono text-[11px] text-muted">{aside}</span> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint }: { label: ReactNode; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="mono-label text-[0.62rem] text-muted">{label}</p>
      <p className="mt-1 truncate font-mono text-2xl text-fg">{value}</p>
      {hint ? <p className="mt-0.5 font-mono text-[11px] text-muted">{hint}</p> : null}
    </div>
  );
}

const outcomeCls: Record<string, string> = {
  success: "border-win/40 text-win",
  rug: "border-rug/40 text-rug",
  dead: "border-flat text-muted",
  neutral: "border-line text-fg/70",
  pending: "border-accent/40 text-accent",
  no_data: "border-line text-muted",
};
const outcomeText: Record<string, string> = {
  success: "win",
  rug: "rug",
  dead: "dead",
  neutral: "neutral",
  pending: "pending",
  no_data: "no data",
};

export function OutcomePill({ label }: { label: string | null | undefined }) {
  if (!label) return <span className="font-mono text-[11px] text-muted">—</span>;
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase ${outcomeCls[label] ?? "border-line text-muted"}`}>
      {outcomeText[label] ?? label}
    </span>
  );
}

const confCls: Record<string, string> = {
  high: "bg-accent text-ink",
  medium: "border border-accent/60 text-accent",
  low: "border border-line text-muted",
};

export function ConfidencePill({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="font-mono text-[11px] text-muted">—</span>;
  return <span className={`inline-flex rounded-full px-2 py-0.5 font-mono text-[10px] uppercase ${confCls[value] ?? confCls.low}`}>{value}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted">{children}</p>;
}

/** Horizontal-scrolling table wrapper so wide tables never overflow the page at 360px. */
export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">{children}</div>;
}

export const th = "mono-label whitespace-nowrap border-b border-line px-3 py-2 text-left text-[0.62rem] font-normal text-muted";
export const td = "whitespace-nowrap border-b border-line/60 px-3 py-2.5 font-mono text-[12px]";
