import { SocialLinks } from "@/components/ui/SocialLinks";

const links = [
  ["How it works", "#how"], ["Signal", "#signal"],
  ["Graph", "#graph"], ["Telegram", "#telegram"], ["Roadmap", "#roadmap"],
];

export function Footer() {
  return (
    <footer className="border-t border-line bg-ink px-4 pb-10 pt-12 text-fg sm:px-8 sm:pt-16">
      <div className="mx-auto max-w-[1256px]">
        <div className="flex justify-between font-sans text-[72px] font-extrabold leading-none tracking-normal min-[400px]:text-[84px] sm:text-[136px] md:text-[168px] lg:text-[228px] xl:text-[286px]" aria-label="Kairos">
          {Array.from("KAIROS").map((letter, index) => <span key={index} aria-hidden="true">{letter}</span>)}
        </div>
        <div className="mt-10 flex flex-col gap-8 border-t border-line pt-8 md:flex-row md:justify-between">
          <nav aria-label="Footer navigation" className="flex flex-wrap gap-x-6 gap-y-4">
            {links.map(([label, href]) => <a key={href} href={href} className="mono-label text-muted transition-colors hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent">{label}</a>)}
          </nav>
          <div className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-4">
            <SocialLinks />
            <p className="mono-label text-muted">© 2026 Kairos</p>
          </div>
        </div>
        <p className="mt-8 max-w-2xl text-sm leading-relaxed text-muted">Kairos does not give financial advice. A signal is statistics on past cases, not a price prediction. Meme tokens can go to zero within minutes. All data on this page is a demo example.</p>
      </div>
    </footer>
  );
}
