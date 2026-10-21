import Link from "next/link";
import { SocialLinks } from "@/components/ui/SocialLinks";

const links = [
  { href: "/app", label: "Overview" },
  { href: "/app/signals", label: "Signals" },
  { href: "/app/tokens", label: "Tokens" },
  { href: "/app/shillers", label: "Shillers" },
  { href: "/app/kols", label: "KOL trades" },
  { href: "/app/narratives", label: "Narratives" },
  { href: "/app/logic", label: "Logic" },
];

export function DashNav() {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-ink/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-[1320px] items-center gap-4 px-4 sm:px-8">
        <Link href="/app" className="flex shrink-0 items-center gap-2 text-sm font-extrabold uppercase tracking-[0.18em]">
          <span className="size-2 rounded-full bg-accent" aria-hidden />
          Kairos
        </Link>
        <nav aria-label="Dashboard" className="-mx-1 flex min-w-0 flex-1 gap-1 overflow-x-auto">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="mono-label shrink-0 rounded-full px-3 py-1.5 text-[0.68rem] text-muted hover:bg-fg/10 hover:text-fg"
            >
              {l.label}
            </Link>
          ))}
        </nav>
        <SocialLinks size="sm" className="shrink-0" />
      </div>
    </header>
  );
}
