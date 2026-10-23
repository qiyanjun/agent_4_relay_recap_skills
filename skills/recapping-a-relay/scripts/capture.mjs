// Capture the flythrough page frame by frame with headless Chrome.
// Usage: node capture.mjs <page_url> <frames_dir> [step]   (step > 1 captures every Nth frame, for a quick preview)
// Env: WORKERS=n captures with n pages (windows of one Chrome) at once, each taking one contiguous stretch of the
// frames. One Chrome rather than n: a second headless Chrome started beside a running one fails to launch on
// macOS ("Failed to launch the browser process: Code: null"). Background throttling is off so no tab stalls.
// The page must set window.sceneReady (or window.sceneError), window.frameCount and window.renderFrame(i);
// window.timeline, if set, is written to <frames_dir>/timeline.json.
//
// Parallel capture is seamless because no worker fits its own cameras: the first page fits them (its cameras and
// label placements depend on which terrain tiles had loaded while fitting) and publishes them as
// window.sceneLayout; every other page is handed that layout as window.presetLayout before its script runs, so
// all of them draw frame i identically.
import puppeteer from "puppeteer-core";
import fs from "node:fs";
import { chromePath } from "./chrome.mjs";

const [url, outDir, stepArg] = process.argv.slice(2);
if (!url || !outDir) {
  console.error("usage: node capture.mjs <page_url> <frames_dir> [step]");
  process.exit(2);
}
const step = Number(stepArg || 1);
const workers = Math.max(1, Math.floor(Number(process.env.WORKERS || 1)));
fs.mkdirSync(outDir, { recursive: true });
const scene = JSON.parse(await (await fetch(new URL("scene.json", url))).text());

const browser = await puppeteer.launch({
  executablePath: chromePath(),
  headless: true,
  defaultViewport: { width: scene.options.width, height: scene.options.height, deviceScaleFactor: 1 },
  args: ["--hide-scrollbars", "--ignore-gpu-blocklist", "--disable-background-timer-throttling",
         "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"],
});
async function openPage(tag, layout) {
  // each worker in its own window: a background tab stops drawing its map and the capture stalls
  const page = tag && tag !== " 1" ? await browser.newPage({ type: "window" }) : await browser.newPage();
  page.on("console", m => console.log(`[page${tag}]`, m.text()));
  page.on("pageerror", e => console.log(`[page${tag} error]`, e.message));
  page.on("response", r => { if (r.status() >= 400) console.log("[http]", r.status(), r.url()); });
  if (layout) await page.evaluateOnNewDocument(l => { window.presetLayout = l; }, layout);
  await page.goto(url, { waitUntil: "load", timeout: 120000 });
  await page.waitForFunction("window.sceneReady === true || !!window.sceneError", { timeout: 600000 });
  const sceneError = await page.evaluate("window.sceneError");
  if (sceneError) throw new Error(`scene failed to load: ${sceneError}`);
  return page;
}

try {
  const first = await openPage(workers > 1 ? " 1" : "", null);
  const frames = await first.evaluate("window.frameCount");
  const timeline = await first.evaluate("window.timeline || null");
  if (timeline) fs.writeFileSync(`${outDir}/timeline.json`, JSON.stringify(timeline, null, 1));
  const layout = await first.evaluate("window.sceneLayout || null");
  if (workers > 1 && !layout) throw new Error("the page publishes no window.sceneLayout; run with WORKERS=1");

  const wanted = [];
  for (let i = 0; i < frames; i += step) wanted.push(i);
  const n = Math.min(workers, wanted.length);
  const pages = [first];
  for (let w = 1; w < n; w++) pages.push(await openPage(` ${w + 1}`, layout));
  for (const p of pages.slice(1)) {
    const f = await p.evaluate("window.frameCount");
    if (f !== frames) throw new Error(`worker pages disagree on the frame count (${f} vs ${frames})`);
  }

  const started = Date.now();
  let shot = 0;
  await Promise.all(pages.map(async (page, w) => {
    const mine = wanted.slice(Math.floor(w * wanted.length / n), Math.floor((w + 1) * wanted.length / n));
    // A page's first frame at a new spot pins its markers before that spot's terrain has loaded, a couple of
    // pixels off; draw it once and throw it away so a worker's first frame matches the one-worker render.
    if (mine.length && w > 0) await page.evaluate(k => window.renderFrame(k), mine[0]);
    for (const i of mine) {
      await page.evaluate(k => window.renderFrame(k), i);
      await page.screenshot({ path: `${outDir}/f_${String(i / step).padStart(5, "0")}.png` });
      shot++;
      if (shot % 30 === 0) {
        const per = (Date.now() - started) / shot / 1000;
        console.log(`frame ${shot}/${wanted.length}, ${per.toFixed(2)} s per frame across ${n} worker(s), `
                  + `~${Math.round(per * (wanted.length - shot) / 60)} min left`);
      }
    }
  }));
  console.log(`captured ${shot} of ${frames} frames in ${((Date.now() - started) / 1000).toFixed(0)} s`
            + (n > 1 ? ` with ${n} workers` : ""));
} finally {
  await browser.close();
}
