import type { Metadata } from "next";
import { Suspense } from "react";
import { DashNav } from "@/components/dash/DashNav";
import { SocialLinks } from "@/components/ui/SocialLinks";

export const metadata: Metadata = { title: "Dashboard — Kairos", robots: { index: false } };

/** Dashboard shell. Public; every page is dynamic (reads the DB per request), hence the Suspense boundary. */
export default function DashLayout({ children }: LayoutProps<"/app">) {
  return (
    <div className="min-h-dvh bg-ink text-fg">
      <DashNav />
      <main className="mx-auto w-full max-w-[1320px] px-4 pb-20 pt-6 sm:px-8">
        <Suspense fallback={<p className="mono-label py-20 text-center text-muted">Loading…</p>}>{children}</Suspense>
      </main>
      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-[1320px] flex-wrap items-center justify-between gap-4 px-4 py-6 sm:px-8">
          <p className="mono-label text-muted">© 2026 Kairos · not financial advice</p>
          <SocialLinks size="sm" />
        </div>
      </footer>
    </div>
  );
}
