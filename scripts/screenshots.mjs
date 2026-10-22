// Screenshot generator for the GitHub repo images: showcase/, calls/, analytics/.
//
// Setup (once):   npm i playwright-core          (no browser download, uses system Chrome)
// Run:            node scripts/screenshots.mjs [--base https://xkairos.io] [--out <repo root>]
//                      [--only landing,dashboard,mobile,calls,analytics] [--shiller <id>]
// Env:            CHROME_PATH  path to a Chrome/Chromium executable
//
// Notes
//  - WebGL (landing hero, /graph) renders on software GL (SwiftShader): slow, so DPR 1 and long timeouts.
//  - On weak machines run it under a CPU/RAM cap and never start two browser runs at once.
//  - One browser, one page at a time; the browser is closed at the end.
//  - Output is JPEG (quality 85, re-taken at 70 if a file exceeds ~400 KB).
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const argv = process.argv.slice(2);
const arg = (name, fallback) => { const i = argv.indexOf(`--${name}`); return i >= 0 ? argv[i + 1] : fallback; };
const BASE = arg("base", "https://xkairos.io").replace(/\/$/, "");
const OUT = path.resolve(arg("out", path.join(import.meta.dirname, "..")));
const ONLY = new Set((arg("only", "landing,dashboard,mobile,calls,analytics")).split(","));
const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const MAX_BYTES = 400 * 1024;

// Real tokens for calls/: market cap chart with shiller avatars. Order = file number.
const CALL_TOKENS = [
  { id: 102, slug: "vpeuub" },
  { id: 47106, slug: "twyne" },
  { id: 79, slug: "5gytac" },
  { id: 99, slug: "hhbds3" },
  { id: 88, slug: "8datxr" },
  { id: 80846, slug: "dream" },
  { id: 262, slug: "schoolbooks" },
  { id: 83, slug: "6vwkhv" },
];
const SHOWCASE_TOKEN = 47106;

// Panels for analytics/: [page path, card title (regex), file name].
const ANALYTICS = [
  ["/app", /^Hot narratives/, "01-hot-narratives.jpg"],
  ["/app", /^Shills by hour/, "02-shills-by-hour.jpg"],
  ["/app", /^Top shillers · 24h/, "03-shiller-mindshare.jpg"],
  ["/app", /^Most shilled tokens/, "04-most-shilled-tokens.jpg"],
  ["/app", /^Collection/, "05-collection-kpis.jpg"],
  ["/app/shillers", /^Shillers by CA/, "06-shillers-by-ca.jpg"],
  ["/app/narratives?window=6h", /^Narrative mindshare/, "07-narrative-mindshare.jpg"],
];

const browser = await chromium.launch({
  executablePath: CHROME,
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);

async function withPage(opts, fn) {
  const ctx = await browser.newContext({ deviceScaleFactor: 1, ...opts });
  const page = await ctx.newPage();
  page.setDefaultTimeout(120000);
  try { await fn(page); } finally { await ctx.close(); }
}
async function open(page, route, settle = 2500) {
  await page.goto(BASE + route, { waitUntil: "load", timeout: 120000 });
  try { await page.waitForLoadState("networkidle", { timeout: 30000 }); } catch { /* live pages keep polling */ }
  // some dashboard pages stream in behind a "Loading…" fallback
  try { await page.waitForFunction(() => !document.body.innerText.includes("Loading…"), null, { timeout: 90000 }); } catch { /* shoot anyway */ }
  await sleep(settle);
  await page.mouse.move(0, 0);
}
// Screenshot with a size guard: retake at lower quality if the JPEG is too big.
async function shoot(file, take) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  for (const quality of [85, 70]) {
    await take(file, quality);
    const size = fs.statSync(file).size;
    if (size <= MAX_BYTES) { log("ok  ", path.relative(OUT, file), Math.round(size / 1024) + " KB"); return; }
  }
  log("big ", path.relative(OUT, file), Math.round(fs.statSync(file).size / 1024) + " KB");
}
const viewportShot = (page, file, extra = {}) =>
  shoot(file, (f, quality) => page.screenshot({ path: f, type: "jpeg", quality, timeout: 120000, ...extra }));
const card = (page, title) => page.locator("section", { has: page.locator("h2", { hasText: title }) }).first();
const cardShot = (page, title, file) =>
  shoot(file, async (f, quality) => {
    const el = card(page, title);
    await el.waitFor({ timeout: 60000 });
    await el.scrollIntoViewIfNeeded();
    await el.screenshot({ path: f, type: "jpeg", quality, timeout: 120000 });
  });
async function taller(page, height) { // grow the viewport so a long page fits in one shot
  const vp = page.viewportSize();
  await page.setViewportSize({ width: vp.width, height });
  await sleep(1200);
}

// ---- showcase/landing -------------------------------------------------------
if (ONLY.has("landing")) {
  await withPage({ viewport: { width: 1440, height: 900 } }, async (page) => {
    const dir = path.join(OUT, "showcase/landing");
    await open(page, "/", 8000); // hero WebGL scene needs a moment
    const sections = [["hero", "01-hero"], ["how", "02-how"], ["signal", "03-signal"], ["calls", "04-calls"],
      ["graph", "05-graph"], ["telegram", "06-telegram"], ["roadmap", "07-roadmap"], ["cta", "08-cta"]];
    const geom = (id) => page.evaluate((i) => { const el = document.getElementById(i); return { top: el.getBoundingClientRect().top + scrollY, h: el.offsetHeight }; }, id);
    for (const [id, name] of sections) {
      // sections taller than the window get a taller viewport (max 1500 px) instead of a cropped shot
      const { h } = await geom(id);
      await page.setViewportSize({ width: 1440, height: id === "hero" ? 900 : Math.min(Math.max(h, 900), 1500) });
      await sleep(1000);
      const { top } = await geom(id);
      await page.evaluate((y) => window.scrollTo(0, y), top);
      await sleep(id === "graph" ? 6000 : 2500); // scroll-in animations / WebGL
      await viewportShot(page, path.join(dir, `desktop-${name}.jpg`));
    }
  });
}

// ---- showcase/dashboard -----------------------------------------------------
if (ONLY.has("dashboard")) {
  await withPage({ viewport: { width: 1440, height: 900 } }, async (page) => {
    const dir = path.join(OUT, "showcase/dashboard");
    const plain = [["/app", "desktop-01-overview.jpg", 1500], ["/app/signals", "desktop-02-signals.jpg", 1000],
      ["/app/tokens", "desktop-03-tokens.jpg", 1000], ["/app/shillers", "desktop-04-shillers.jpg", 1500]];
    for (const [route, name, h] of plain) {
      await open(page, route);
      await taller(page, h);
      await viewportShot(page, path.join(dir, name));
      await page.setViewportSize({ width: 1440, height: 900 });
    }
    // shiller detail: first row of the leaderboard (default sort = Wilson lower bound) unless --shiller is given
    let shiller = arg("shiller");
    if (!shiller) {
      await open(page, "/app/shillers", 500);
      const href = await page.locator('table a[href^="/app/shillers/"]').first().getAttribute("href");
      shiller = href.split("/").pop();
    }
    log("shiller id:", shiller);
    await open(page, `/app/shillers/${shiller}`);
    await taller(page, 1500);
    await viewportShot(page, path.join(dir, "desktop-05-shiller.jpg"));
    await page.setViewportSize({ width: 1440, height: 900 });
    for (const [route, name, h] of [["/app/narratives?window=6h", "desktop-06-narratives.jpg", 1300],
      ["/app/logic", "desktop-07-logic.jpg", 1000]]) {
      await open(page, route);
      await taller(page, h);
      await viewportShot(page, path.join(dir, name));
      await page.setViewportSize({ width: 1440, height: 900 });
    }
    await open(page, "/graph", 15000); // WebGL graph, slow on software GL
    await viewportShot(page, path.join(dir, "desktop-08-graph.jpg"));
    await open(page, `/app/tokens/${SHOWCASE_TOKEN}`, 3500);
    await taller(page, 1500);
    await viewportShot(page, path.join(dir, "desktop-09-token.jpg"));
  });
}

// ---- showcase/mobile --------------------------------------------------------
if (ONLY.has("mobile")) {
  await withPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }, async (page) => {
    const dir = path.join(OUT, "showcase/mobile");
    await open(page, "/", 8000);
    await viewportShot(page, path.join(dir, "mobile-01-landing.jpg"));
    await open(page, "/app");
    await viewportShot(page, path.join(dir, "mobile-02-dashboard.jpg"));
    await open(page, `/app/tokens/${SHOWCASE_TOKEN}`, 3500);
    await card(page, "Market cap & calls").evaluate((el) => el.scrollIntoView()); // chart at the top of the screen
    await sleep(1500);
    await viewportShot(page, path.join(dir, "mobile-03-token.jpg"));
  });
}

// ---- calls/ -----------------------------------------------------------------
if (ONLY.has("calls")) {
  await withPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5 }, async (page) => {
    const facts = [];
    let n = 0;
    for (const { id, slug } of CALL_TOKENS) {
      n++;
      await open(page, `/app/tokens/${id}`, 3500);
      const text = await page.innerText("main");
      const pick = (re) => (text.match(re) || [])[1];
      facts.push({
        n, id, slug,
        symbol: text.split("\n")[0],
        shillers: pick(/SHILLERS\s*\n\s*(\d+)/),
        peak: pick(/PEAK SINCE FIRST CALL\s*\n\s*(\S+)/),
        peakDetail: pick(/PEAK SINCE FIRST CALL\s*\n\s*\S+\s*\n\s*(.*)/),
        now: pick(/MARKET CAP\s*\n\s*(\S+)/),
        sinceFirstCall: pick(/MARKET CAP\s*\n\s*\S+\s*\n\s*(.*)/),
        score: pick(/KAIROS SCORE\s*\n\s*([\d.]+)/),
        firstCall: pick(/^FIRST CALL\s*\n\s*(.+)/m),
      });
      await card(page, "Market cap & calls").locator("canvas").first().waitFor();
      await cardShot(page, "Market cap & calls", path.join(OUT, "calls", `${String(n).padStart(2, "0")}-${slug}.jpg`));
    }
    log(JSON.stringify(facts, null, 1));
  });
}

// ---- analytics/ -------------------------------------------------------------
if (ONLY.has("analytics")) {
  await withPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5 }, async (page) => {
    let current = "";
    for (const [route, title, name] of ANALYTICS) {
      if (route !== current) { await open(page, route); current = route; }
      await cardShot(page, title, path.join(OUT, "analytics", name));
    }
  });
}

await browser.close();
