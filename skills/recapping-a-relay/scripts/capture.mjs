// Capture the flythrough page frame by frame with headless Chrome.
// Usage: node capture.mjs <page_url> <frames_dir> [step]   (step > 1 captures every Nth frame, for a quick preview)
// The page must set window.sceneReady (or window.sceneError), window.frameCount and window.renderFrame(i).
import puppeteer from "puppeteer-core";
import fs from "node:fs";
import { chromePath } from "./chrome.mjs";

const [url, outDir, stepArg] = process.argv.slice(2);
if (!url || !outDir) {
  console.error("usage: node capture.mjs <page_url> <frames_dir> [step]");
  process.exit(2);
}
const step = Number(stepArg || 1);
fs.mkdirSync(outDir, { recursive: true });
const scene = JSON.parse(await (await fetch(new URL("scene.json", url))).text());

const browser = await puppeteer.launch({
  executablePath: chromePath(),
  headless: true,
  defaultViewport: { width: scene.options.width, height: scene.options.height, deviceScaleFactor: 1 },
  args: ["--hide-scrollbars", "--ignore-gpu-blocklist"],
});
try {
  const page = await browser.newPage();
  page.on("console", m => console.log("[page]", m.text()));
  page.on("pageerror", e => console.log("[page error]", e.message));
  page.on("response", r => { if (r.status() >= 400) console.log("[http]", r.status(), r.url()); });
  await page.goto(url, { waitUntil: "load", timeout: 120000 });
  await page.waitForFunction("window.sceneReady === true || !!window.sceneError", { timeout: 600000 });
  const sceneError = await page.evaluate("window.sceneError");
  if (sceneError) throw new Error(`scene failed to load: ${sceneError}`);
  const frames = await page.evaluate("window.frameCount");
  const started = Date.now();
  let shot = 0;
  for (let i = 0; i < frames; i += step) {
    await page.evaluate(k => window.renderFrame(k), i);
    await page.screenshot({ path: `${outDir}/f_${String(shot).padStart(5, "0")}.png` });
    shot++;
    if (shot % 30 === 0) {
      const per = (Date.now() - started) / shot / 1000;
      console.log(`frame ${i + 1}/${frames}, ${per.toFixed(2)} s per frame, ~${Math.round(per * (frames - i) / step / 60)} min left`);
    }
  }
  console.log(`captured ${shot} of ${frames} frames in ${((Date.now() - started) / 1000).toFixed(0)} s`);
} finally {
  await browser.close();
}
