import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1280, height: 900 } });
const errs = [];
p.on("pageerror", (e) => { errs.push(e.message); console.log("PAGEERROR:", e.message.split("\n")[0]); });
await p.goto("http://localhost:5199/perf.html");
await p.waitForSelector('[data-testid="doc-0"]');

const scroller = () => {
  const els = [...document.querySelectorAll('[data-testid="host"] div')];
  return els.find((e) => e.scrollHeight > e.clientHeight + 50 && getComputedStyle(e).overflowY === "auto");
};

console.log("-- open big, scroll to bottom, then click tiny");
await p.click('[data-testid="doc-0"]');
await p.waitForTimeout(800);
await p.evaluate(`(${scroller.toString()})().scrollTop = 100000`);
await p.waitForTimeout(800);
await p.evaluate(`(${scroller.toString()})().scrollTop = 140000`);
await p.waitForTimeout(800);
console.log("scrollTop now:", await p.evaluate(`(${scroller.toString()})()?.scrollTop`));
await p.click('[data-testid="doc-1"]');
await p.waitForTimeout(1000);
const txt = await p.evaluate(() => document.querySelector('[data-testid="host"]')?.innerText?.slice(0,60) ?? "GONE");
console.log("after switch:", JSON.stringify(txt.replace(/\n/g," ")));
console.log("errors:", errs.length);
await b.close();
