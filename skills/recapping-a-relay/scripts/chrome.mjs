// Find a Chrome/Chromium binary for puppeteer-core: $CHROME first, then the usual install locations.
import fs from "node:fs";

const CANDIDATES = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
];

export function chromePath() {
  const found = [process.env.CHROME, ...CANDIDATES].find(p => p && fs.existsSync(p));
  if (!found) throw new Error("Chrome not found; set CHROME=/path/to/chrome");
  return found;
}
