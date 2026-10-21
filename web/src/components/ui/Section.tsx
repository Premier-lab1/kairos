import type { ReactNode } from "react";

type Tone = "dark" | "light";

type SectionProps = {
  id: string;
  tone?: Tone;
  children: ReactNode;
  className?: string;
};

/**
 * Page section. Light sections get a rounded top that overlaps the dark section above
 * (Exlipse-style); the section after a light one should simply follow.
 */
export function Section({ id, tone = "dark", children, className = "" }: SectionProps) {
  const toneCls =
    tone === "light"
      ? "relative z-10 -mt-8 rounded-t-[2rem] bg-paper text-ink sm:-mt-10 sm:rounded-t-[2.75rem]"
      : "relative bg-ink text-fg";
  return (
    <section id={id} data-tone={tone} className={`${toneCls} scroll-mt-20 ${className}`}>
      <div className="mx-auto w-full max-w-[1320px] px-4 py-20 sm:px-8 sm:py-28 lg:py-32">{children}</div>
    </section>
  );
}

type HeadingProps = {
  /** "01" */
  index: string;
  /** small mono label next to the index, e.g. "How it works" */
  kicker: string;
  /** big uppercase title; wrap the light part in <em> */
  title: ReactNode;
  lead?: ReactNode;
  tone?: Tone;
};

export function SectionHeading({ index, kicker, title, lead, tone = "dark" }: HeadingProps) {
  const muted = tone === "light" ? "text-ink/60" : "text-muted";
  return (
    <header className="mb-12 sm:mb-16">
      <p className={`mono-label mb-6 flex items-center gap-3 ${muted}`}>
        <span className="text-accent">{index}</span>
        <span className={`h-px w-8 ${tone === "light" ? "bg-ink/30" : "bg-line"}`} />
        <span>{kicker}</span>
      </p>
      <h2 className="headline max-w-[16ch]">{title}</h2>
      {lead ? <p className={`mt-6 max-w-[56ch] text-base sm:text-lg ${muted}`}>{lead}</p> : null}
    </header>
  );
}
