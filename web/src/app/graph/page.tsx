import type { Metadata } from "next";
import { Suspense } from "react";
import { GraphAnimation } from "@/components/graph/GraphAnimation";
import { GraphArticleLegend } from "@/components/graph/GraphLegend";

export const metadata: Metadata = {
  title: "Kairos — shiller graph",
  robots: { index: false },
};

type Query = Record<string, string | string[] | undefined>;

function dimension(value: Query[string]): number | undefined {
  if (typeof value !== "string" || !/^\d+$/.test(value)) return;
  const size = Number(value);
  // Reject invalid or impractically large canvas sizes; use the viewport instead.
  return Number.isSafeInteger(size) && size > 0 && size <= 8192 ? size : undefined;
}

type GraphPageProps = { searchParams: Promise<Query> };

export default function GraphPage(props: GraphPageProps) {
  return (
    <Suspense fallback={
      <main className="fixed inset-0 bg-ink">
        <p className="absolute bottom-2 right-3 font-mono text-[10px] text-muted">Illustrative data</p>
      </main>
    }>
      <GraphContent {...props} />
    </Suspense>
  );
}

async function GraphContent({ searchParams }: GraphPageProps) {
  const query = await searchParams;
  const width = dimension(query.w);
  const height = dimension(query.h);
  const fixed = width !== undefined && height !== undefined;
  const legend = query.legend !== "0";
  // ?scale=1.6 — larger labels for screenshots; default slightly larger than on the landing page.
  const rawScale = Number(typeof query.scale === "string" ? query.scale : NaN);
  const labelScale = Number.isFinite(rawScale) ? Math.min(2.5, Math.max(0.8, rawScale)) : 1.3;

  return (
    <main className="fixed inset-0 flex overflow-auto bg-ink text-fg">
      <div
        className="relative m-auto shrink-0 overflow-hidden bg-ink"
        style={{
          width: fixed ? width : "100%",
          height: fixed ? height : "100dvh",
          backgroundImage: "radial-gradient(ellipse at 50% 45%, rgba(255,79,23,0.07), transparent 62%)",
        }}
      >
        {legend ? (
          <h1 className="pointer-events-none absolute inset-x-0 top-5 z-10 px-5 text-center font-mono text-xs text-muted sm:text-sm">
            Kairos · shiller → token graph
          </h1>
        ) : null}
        <div className="absolute inset-0">
          <GraphAnimation showLegend={legend} labelScale={labelScale} />
        </div>
        {legend ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-8 px-5 text-center">
            <GraphArticleLegend />
          </div>
        ) : null}
        <p className="pointer-events-none absolute bottom-2 right-3 font-mono text-[10px] text-muted">
          Illustrative data
        </p>
      </div>
    </main>
  );
}
