import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1280, height: 900 } });
const errs = [];
p.on("pageerror", (e) => { errs.push(e.message); console.log("PAGEERROR:", e.message.split("\n")[0]); });
p.on("console", (m) => { if (m.type() === "error") console.log("CONSOLE.ERR:", m.text().slice(0,220)); });
await p.goto("http://localhost:5201/");
await p.waitForTimeout(1500);
const has = await p.$('[data-testid="doc-0"]');
console.log("buttons present:", !!has);
if (has) {
  for (const k of [0,1,2,0,1]) {
    await p.click(`[data-testid="doc-${k}"]`);
    await p.waitForTimeout(700);
    const t = await p.evaluate(() => document.querySelector('[data-testid="host"]')?.innerText?.slice(0,45) ?? "GONE");
    console.log(`doc-${k}:`, JSON.stringify(t.replace(/\n/g," ")));
  }
}
console.log("errors:", errs.length);
await b.close();
