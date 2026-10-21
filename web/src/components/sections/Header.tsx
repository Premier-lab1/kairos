import { SocialLinks } from "@/components/ui/SocialLinks";

const nav = [
  { href: "#how", label: "How it works" },
  { href: "#signal", label: "Signal" },
  { href: "#graph", label: "Graph" },
  { href: "#telegram", label: "Bot" },
  { href: "#roadmap", label: "Roadmap" },
];

/** Floating top bar: wordmark left, mini nav pill in the centre (Dragonfly-like), CTA right. */
export function Header() {
  return (
    <header className="pointer-events-none fixed inset-x-0 top-0 z-50">
      <div className="mx-auto flex h-16 max-w-[1320px] items-center justify-between gap-4 px-4 sm:px-8">
        <a
          href="#top"
          className="pointer-events-auto flex h-9 items-center gap-2 rounded-full border border-fg/10 bg-ink/70 px-4 text-sm font-extrabold uppercase tracking-[0.18em] text-fg backdrop-blur-md"
        >
          <span className="size-2 rounded-full bg-accent" aria-hidden />
          Kairos
        </a>
        <nav
          aria-label="Sections"
          className="pointer-events-auto hidden items-center gap-1 rounded-full border border-fg/10 bg-ink/70 p-1 backdrop-blur-md md:flex"
        >
          {nav.map((n) => (
            <a
              key={n.href}
              href={n.href}
              className="mono-label rounded-full px-3 py-1.5 text-[0.68rem] text-muted transition-colors hover:bg-fg/10 hover:text-fg"
            >
              {n.label}
            </a>
          ))}
        </nav>
        <div className="pointer-events-auto flex items-center gap-2">
          <SocialLinks size="sm" />
          <a
            href="/app"
            className="mono-label inline-flex h-9 items-center rounded-full bg-accent px-4 text-[0.68rem] font-medium text-ink hover:bg-[#ff6a3a]"
          >
            <span className="whitespace-nowrap">
              <span className="hidden sm:inline">Open </span>dashboard
            </span>
          </a>
        </div>
      </div>
    </header>
  );
}
