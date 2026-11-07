const PROFILES = {
  high: {minimum: 2, maximum: 2.5, supersampling: 1.25},
  balanced: {minimum: 1, maximum: 1, supersampling: 1},
};
const MAX_PIXELS = 8_000_000;

export function resolveRenderQuality({quality = "high", cssWidth, cssHeight,
  devicePixelRatio = 1, maxDimension = Infinity}) {
  if (![cssWidth, cssHeight].every(value => Number.isFinite(value) && value > 0)
      || !(maxDimension > 0)) return null;
  const profile = Object.hasOwn(PROFILES, quality) ? PROFILES[quality] : PROFILES.high;
  const nativeRatio = Number.isFinite(devicePixelRatio) && devicePixelRatio > 0 ? devicePixelRatio : 1;
  const requestedRatio = Math.min(profile.maximum,
    Math.max(profile.minimum, nativeRatio * profile.supersampling));
  const pixelRatio = Math.min(requestedRatio,
    Math.sqrt(MAX_PIXELS / cssWidth / cssHeight),
    maxDimension / Math.max(cssWidth, cssHeight));
  return pixelRatio > 0 ? {pixelRatio} : null;
}
