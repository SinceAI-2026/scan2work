import assert from "node:assert/strict";
import {resolveRenderQuality} from "../web/render-quality.mjs";

const viewport = {cssWidth: 1224, cssHeight: 772, devicePixelRatio: 2, maxDimension: 16384};
const resolve = extra => resolveRenderQuality({...viewport, ...extra});
const buffer = (input, quality) => ({
  width: Math.floor(input.cssWidth * quality.pixelRatio),
  height: Math.floor(input.cssHeight * quality.pixelRatio),
});

assert.deepEqual(buffer(viewport, resolve({quality: "high"})), {width: 3060, height: 1930});
assert.deepEqual(buffer(viewport, resolve({quality: "balanced"})), {width: 1224, height: 772});
assert.equal(resolve({devicePixelRatio: 1}).pixelRatio, 2);
assert.equal(resolve({devicePixelRatio: 4}).pixelRatio, 2.5);
assert.equal(resolve({quality: "unknown"}).pixelRatio, resolve({quality: "high"}).pixelRatio);
assert.equal(resolve({quality: "toString"}).pixelRatio, resolve({quality: "high"}).pixelRatio);
for (const ratio of [0, -1, NaN, Infinity])
  assert.equal(resolve({devicePixelRatio: ratio}).pixelRatio, 2);

for (const quality of ["high", "balanced"]) {
  const input = {...viewport, cssWidth: 10000, cssHeight: 6000, quality};
  const size = buffer(input, resolveRenderQuality(input));
  assert.ok(size.width * size.height <= 8_000_000, "large views stay within the pixel budget");
  assert.ok(size.width > 0 && size.height > 0);
  const limited = {...viewport, quality, maxDimension: 1024};
  const bounded = buffer(limited, resolveRenderQuality(limited));
  assert.ok(bounded.width <= 1024 && bounded.height <= 1024, "hardware bounds override quality");
}

const fractional = {...viewport, cssWidth: 811.75, cssHeight: 603.25, devicePixelRatio: 1.75};
const fractionalSize = buffer(fractional, resolveRenderQuality(fractional));
assert.deepEqual(fractionalSize, {width: 1775, height: 1319});
for (const cssWidth of [0, -1, NaN, Infinity]) assert.equal(resolve({cssWidth}), null);
for (const cssHeight of [0, -1, NaN, Infinity]) assert.equal(resolve({cssHeight}), null);
for (const maxDimension of [0, -1, NaN]) assert.equal(resolve({maxDimension}), null);

console.log("Render quality passed: High/Balanced sampling, fractional sizing, pixel budget, hardware bounds, and hidden views.");
