// Keep native video evidence distinct from a rendered segmentation view.
export function renderAssetPhoto(asset, figure, baseUrl) {
  const image = figure.querySelector("img");
  const caption = figure.querySelector("figcaption");
  image.onload = image.onerror = null;
  image.removeAttribute("src");
  image.hidden = true;
  figure.hidden = true;
  figure.querySelectorAll(".evidence-controls,.evidence-note").forEach(el => el.remove());
  const evidence = Array.isArray(asset?.evidence) ? asset.evidence.filter(e => e.cropUrl || e.imageUrl || e.renderedcropUrl || e.renderedCropUrl) : [];
  if (!evidence.length && asset?.photo) evidence.push({cropUrl:typeof asset.photo === "string" ? asset.photo : asset.photo.url,ptsSeconds:asset.photo_source?.video_t_s,kind:asset.photo.kind || asset.photo_source?.kind || "video"});
  if (!evidence.length) return;
  const controls = document.createElement("div");
  controls.className = "evidence-controls";
  figure.appendChild(controls);
  const note = document.createElement("p");
  note.className = "evidence-note";
  note.textContent = asset?.source_region_id ? "Source regions may include nearby objects. A video link is evidence, not a verified 3D camera alignment." : "";
  figure.appendChild(note);
  let generation = 0;
  const show = index => {
    const e = evidence[index], mine = ++generation;
    const path = e.cropUrl || e.imageUrl || e.renderedcropUrl || e.renderedCropUrl;
    let url;
    try { url = new URL(path, baseUrl); } catch { return; }
    if (!["http:","https:"].includes(url.protocol)) return;
    const timestamp = e.ptsSeconds ?? e.video_t_s ?? e.timestamp ?? asset?.photo_source?.video_t_s;
    const rendered = String(e.kind || "").startsWith("render") || e.source === "render" || Boolean(e.renderedcropUrl || e.renderedCropUrl);
    image.hidden = true;
    image.alt = `${asset?.label || asset?.id || "Region"} · ${rendered ? "rendered source view" : "source video evidence"}`;
    caption.textContent = `${rendered ? "Rendered scan view" : "Original video frame"}${Number.isFinite(timestamp) ? ` · ${timestamp.toFixed(2)} s` : ""} · ${index + 1}/${evidence.length}`;
    image.onload = () => { if (mine === generation) image.hidden = false; };
    image.onerror = () => { if (mine === generation) caption.textContent = "Evidence image unavailable in this bundle."; };
    image.src = url.href;
    figure.hidden = false;
    controls.querySelectorAll("button").forEach((button, i) => button.setAttribute("aria-current", String(i === index)));
    controls.querySelector("a")?.remove();
    if (e.imageUrl) {
      const link = document.createElement("a");
      const full = new URL(e.imageUrl, baseUrl);
      if (["http:","https:"].includes(full.protocol)) {
        link.href = full.href; link.target = "_blank"; link.rel = "noopener"; link.textContent = "Full frame ↗";
        controls.appendChild(link);
      }
    }
  };
  evidence.forEach((_, index) => {
    const button = document.createElement("button"); button.type = "button";
    button.textContent = `View ${index + 1}`; button.addEventListener("click", () => show(index));
    controls.appendChild(button);
  });
  show(0);
}
