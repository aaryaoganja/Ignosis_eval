// Re-renders ../Ignosis_Voice_AI_Quality_Evaluator_Final.pdf from deck.html (10 landscape pages, 1280x720 px).
// Needs playwright-core and a Chromium (set CHROMIUM to its path). Fonts: put static Inter / JetBrains Mono files and
// a fonts/static.css (families InterS, JBMS) next to deck.html; without them the deck falls back to Liberation Sans.
const path = require("path");
const { chromium } = require("playwright-core");
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROMIUM });
  const p = await b.newPage({ viewport: { width: 1280, height: 720 } });
  await p.goto("file://" + path.join(__dirname, "deck.html"));
  await p.waitForLoadState("networkidle"); await p.evaluate(() => document.fonts.ready);
  await p.pdf({ path: path.join(__dirname, "..", "Ignosis_Voice_AI_Quality_Evaluator_Final.pdf"), width: "1280px", height: "720px", printBackground: true, preferCSSPageSize: true });
  await b.close();
})();
