// Playwright driver for web/tools/crop_render.html. Usage: node render_crops.mjs <webDir> <outDir>
import { createRequire } from "module";
const { chromium } = createRequire(import.meta.url)("playwright");
import { createServer } from "http";
import { readFileSync, writeFileSync, existsSync } from "fs";
import { join, extname } from "path";
const [webDir, outDir] = process.argv.slice(2);
const types = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json", ".bin": "application/octet-stream" };
const srv = createServer((q, r) => {
  const f = join(webDir, decodeURIComponent(q.url.split("?")[0]));
  if (!existsSync(f)) { r.writeHead(404); return r.end(); }
  r.writeHead(200, { "Content-Type": types[extname(f)] || "application/octet-stream" }); r.end(readFileSync(f));
}).listen(0);
const port = srv.address().port;
const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1024, height: 1024 } });
await page.goto(`http://127.0.0.1:${port}/tools/crop_render.html`);
await page.waitForFunction(() => window.ready);
const jobs = JSON.parse(readFileSync(join(webDir, "data/crop_jobs/jobs.json")));
let n = 0;
for (const job of jobs) {
  const urls = await page.evaluate(async (job) => {
    const buf = await (await fetch(job.bin)).arrayBuffer();
    return job.views.map(v => window.renderCrop(buf, v));
  }, job);
  writeFileSync(join(outDir, `${job.id}.jpg`), Buffer.from(urls[0].split(",")[1], "base64"));
  if (++n % 50 === 0) console.log(`  rendered ${n}/${jobs.length}`);
}
await browser.close(); srv.close();
console.log(`rendered ${n} objects`);
