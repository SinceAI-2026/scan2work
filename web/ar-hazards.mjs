import { energyDescription } from './hazard-layer.mjs';
import { summaryFor } from './device-summary.mjs';

// This is target context, not an estimate of the target's location in the video.
export function arHazardContext(asset, context) {
  const summary = summaryFor(asset, context);
  const record = context?.devices?.[asset?.id];
  if (!summary || summary.unavailable || !record || record.asset_id !== asset.asset_id)
    return { available: false, title: 'Target context unavailable', detail: 'No matching equipment data is available.', source: '' };
  const energy = energyDescription(record);
  return {
    available: true,
    title: `${asset.tag || asset.asset_id} · ${summary.condition}`,
    issue: summary.issue,
    detail: record.hazards?.length ? energy.reading : 'No potential-energy record available',
    source: record.hazards?.length ? energy.source : `Synthetic workbook snapshot · ${summary.snapshot}`,
    next: summary.next,
  };
}

export function bindArHazards({ button, panel, asset, context }) {
  let enabled = true;
  function render() {
    const data = arHazardContext(asset(), context());
    panel.hidden = !enabled;
    button.setAttribute('aria-pressed', String(enabled));
    button.textContent = enabled ? 'Hide hazard context' : 'Show hazard context';
    panel.querySelector('[data-ar-title]').textContent = data.title;
    panel.querySelector('[data-ar-issue]').textContent = data.available ? `Alert: ${data.issue}` : data.detail;
    panel.querySelector('[data-ar-reading]').textContent = data.available ? data.detail : '';
    panel.querySelector('[data-ar-source]').textContent = data.source;
    panel.querySelector('[data-ar-next]').textContent = data.available ? `Next check: ${data.next}` : '';
    panel.querySelector('[data-ar-diagram]').hidden = !data.available;
  }
  button.addEventListener('click', () => { enabled = !enabled; render(); });
  render();
  return { render };
}
