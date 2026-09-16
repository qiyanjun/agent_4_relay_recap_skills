// PNG snapshots of the route map page for places a link or an HTML file doesn't work (group chats, slides).
// Usage: node snapshot_map.mjs <route_map.html> <out_prefix>
// Writes, in the light theme:
//   <out_prefix>_phone.png     the whole page at phone width (430 px CSS, 2x), leg table fully expanded
//   <out_prefix>_overview.png  desktop header, result and leg strip (1440 px CSS, 2x): a shareable summary card
//   <out_prefix>_map.png       desktop map with the leg table beside it (1440 px CSS, 2x)
import puppeteer from "puppeteer-core";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromePath } from "./chrome.mjs";

const [htmlArg, prefix] = process.argv.slice(2);
if (!htmlArg || !prefix) {
  console.error("usage: node snapshot_map.mjs <route_map.html> <out_prefix>");
  process.exit(2);
}
const url = pathToFileURL(path.resolve(htmlArg)).href;
const browser = await puppeteer.launch({ executablePath: chromePath(), headless: true, args: ["--hide-scrollbars"] });
const pageErrors = [];

async function open(width, height) {
  const page = await browser.newPage();
  page.on("pageerror", e => { console.log("[page error]", e.message); pageErrors.push(e.message); });
  await page.setViewport({ width, height, deviceScaleFactor: 2 });
  await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }, { name: "prefers-reduced-motion", value: "reduce" }]);
  await page.goto(url, { waitUntil: "networkidle0", timeout: 60000 }).catch(() => page.goto(url, { waitUntil: "load" }));
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await new Promise(r => setTimeout(r, 600));  // layout pass: strip labels, plate relaxing, chart draw
  return page;
}

try {
  const phone = await open(430, 932);
  // a picture can't scroll sideways: expand the leg table, drop the optional columns and tighten the rest
  await phone.addStyleTag({ content: `.ledger-scroll { max-height: none !important; }
    th[data-col="rating"], td.rating, th[data-col="hm_pace"], td.hm { display: none; }
    table { font-size: 11.5px; } tbody td, thead th { padding-left: 5px !important; padding-right: 5px !important; }
    td.runner, td.name, td.legs { font-size: 12px; } .chip { font-size: 10px; gap: 4px; }` });
  await phone.addStyleTag({ content: "#ledger-body .rdot { display: none; }" });  // runner color is already on the row tint
  await phone.evaluate(() => document.querySelectorAll("#ledger-body .chip").forEach(c => { if (c.textContent === "Part rebuilt") c.textContent = "Part"; }));
  await new Promise(r => setTimeout(r, 300));
  await phone.screenshot({ path: `${prefix}_phone.png`, fullPage: true });

  const desk = await open(1440, 900);
  // the leg table's last column overflows the ledger by a few px at this width; a picture can't scroll to it
  await desk.addStyleTag({ content: "#ledger-body td, #ledger thead th { padding-left: 6px !important; padding-right: 6px !important; }" });
  await desk.evaluate(() => document.querySelectorAll("#ledger-body .chip").forEach(c => { if (c.textContent === "Part rebuilt") c.textContent = "Part"; }));
  await new Promise(r => setTimeout(r, 300));
  const box = await desk.evaluate(() => {
    const top = document.querySelector(".masthead").getBoundingClientRect();
    const course = document.getElementById("course").getBoundingClientRect();
    return { x: 0, y: Math.max(0, top.top + scrollY - 32), width: document.documentElement.clientWidth, height: course.bottom - top.top + 64 };
  });
  await desk.screenshot({ path: `${prefix}_overview.png`, clip: box, captureBeyondViewport: true });
  const atlas = await desk.$(".atlas");
  await atlas.screenshot({ path: `${prefix}_map.png`, captureBeyondViewport: true });
  console.log(`${prefix}_phone.png, ${prefix}_overview.png, ${prefix}_map.png`);
  if (pageErrors.length) process.exitCode = 1;
} finally {
  await browser.close();
}
