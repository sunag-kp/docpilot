// Capture README screenshots and a demo video from the running stack (`make up` first).
//   node scripts/screenshots.mjs [baseUrl]
// Uses playwright-core with a locally installed Chromium (CHROMIUM_PATH to override).
import { mkdirSync, readdirSync, renameSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../..");
const out = join(root, "docs/images");
const corpus = join(root, "eval/corpus");
const baseUrl = process.argv[2] ?? "http://localhost:8000";
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH ?? "/opt/pw-browsers/chromium" });
const context = await browser.newContext({
  viewport: { width: 1280, height: 760 },
  recordVideo: { dir: join(out, "video"), size: { width: 1280, height: 760 } },
});
const page = await context.newPage();
const shot = (name) => page.screenshot({ path: join(out, `${name}.png`) });

await page.goto(baseUrl);
await page.getByLabel("Workspace name").pressSequentially("Northwind Labs", { delay: 25 });
await page.getByLabel("Email").pressSequentially(`demo+${Date.now()}@northwind.example`, { delay: 15 });
await page.getByLabel("Password").pressSequentially("correct-horse-battery", { delay: 15 });
await shot("signup");
await page.getByRole("button", { name: "Create workspace" }).click();

const files = readdirSync(corpus).map((name) => join(corpus, name));
await page.locator('input[type="file"]').setInputFiles(files);
await page.waitForTimeout(250);
await shot("ingesting");
await page.waitForFunction(
  (n) => [...document.querySelectorAll('[aria-label="Documents"] li')].filter((li) => li.textContent?.includes("ready")).length >= n,
  files.length,
  { timeout: 60_000 },
);
await shot("documents-ready");

async function ask(question) {
  const before = await page.locator("article").count();
  await page.getByLabel("Question").pressSequentially(question, { delay: 18 });
  await page.getByRole("button", { name: "Ask" }).click();
  await page.locator("article").nth(before).getByText(/ms$/).waitFor({ timeout: 30_000 });
  await page.waitForTimeout(400);
}

await ask("How much paid time off do full-time staff earn each month?");
await ask("What does error NW-4012 mean?");
await shot("chat");
await page.locator("article").nth(1).getByRole("button", { name: "1", exact: true }).first().click();
await page.waitForTimeout(500);
await shot("citation");
await page.keyboard.press("Escape");
await ask("What is the CEO's annual salary?");
await shot("refusal");
await page.waitForTimeout(600);

const video = page.video();
await context.close();
await browser.close();
renameSync(await video.path(), join(out, "video", "walkthrough.webm"));
console.log(`screenshots written to ${out}`);
