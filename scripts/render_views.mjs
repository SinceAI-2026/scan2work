// Playwright driver: renders web/data/view_jobs/views.json with web/tools/crop_render.html
import { createRequire } from "module";
const { chromium } = createRequire(import.meta.url)("playwright");
import { createServer } from "http";
import { readFileSync, writeFileSync, existsSync } from "fs";
import { join, extname } from "path";
const [webDir, outDir] = process.argv.slice(2);
const types = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json" };
const srv = createServer((q, r) => {
  const f = join(webDir, decodeURIComponent(q.url.split("?")[0]));
  if (!existsSync(f)) { r.writeHead(404); return r.end(); }
  r.writeHead(200, { "Content-Type": types[extname(f)] || "application/octet-stream" }); r.end(readFileSync(f));
}).listen(0);
const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--js-flags=--max-old-space-size=4096"] });
const page = await browser.newPage({ viewport: { width: 1024, height: 768 } });
await page.goto(`http://127.0.0.1:${srv.address().port}/tools/crop_render.html`);
await page.waitForFunction(() => window.ready);
await page.evaluate(async () => { window.__room = await (await fetch("../data/view_jobs/room.bin")).arrayBuffer(); });
const views = JSON.parse(readFileSync(join(webDir, "data/view_jobs/views.json")));
const cams = [];
for (let i = 0; i < views.length; i++) {
  const out = await page.evaluate((v) => window.renderCrop(window.__room, v), views[i]);
  const name = `view_${String(i).padStart(2, "0")}.jpg`;
  writeFileSync(join(outDir, name), Buffer.from(out.image.split(",")[1], "base64"));
  cams.push({ file: name, W: views[i].W, H: views[i].H, eye: views[i].eye, view: out.view, proj: out.proj });
  console.log(`  view ${i + 1}/${views.length}`);
}
writeFileSync(join(outDir, "cameras.json"), JSON.stringify(cams));
await browser.close(); srv.close();
