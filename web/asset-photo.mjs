export function renderAssetPhoto(asset, figure, baseUrl) {
  const image = figure.querySelector("img");
  image.onload = image.onerror = null;
  image.removeAttribute("src");
  image.hidden = true;
  figure.hidden = true;
  if (typeof asset?.photo !== "string" || !asset.photo.trim()) return;
  let url;
  try { url = new URL(asset.photo, baseUrl); } catch { return; }
  if (!["http:","https:"].includes(url.protocol)) return;
  image.alt = `${asset.tag} in the real walkthrough`;
  const timestamp = asset.photo_source?.video_t_s;
  figure.querySelector("figcaption").textContent = Number.isFinite(timestamp)
    ? `Real walkthrough frame · ${timestamp.toFixed(2)} s`
    : "Real walkthrough frame";
  image.onload = () => { image.hidden = false; };
  image.onerror = () => { figure.hidden = true; };
  image.src = url.href;
  figure.hidden = false;
}
