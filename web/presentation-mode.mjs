/** Presentation only: never changes the scene, selected equipment, routes or service records. */
export const INFORMATION_MODE_KEY = 'scan2work:information-mode:v1';
export function normalizeInformationMode(value) { return value === 'extended' ? 'extended' : 'simple'; }
export function createInformationPreference(storage) {
  let mode = 'simple';
  try { mode = normalizeInformationMode(storage?.getItem(INFORMATION_MODE_KEY)); } catch { /* private browser */ }
  return { get mode() { return mode; }, set(value) {
    mode = normalizeInformationMode(value);
    try { storage?.setItem(INFORMATION_MODE_KEY, mode); } catch { /* session-only preference */ }
    return mode;
  } };
}
export function inspectionAction(selected, fallback, simple) {
  const asset = simple ? selected || fallback : fallback;
  return { asset, label: simple ? asset?.mapped ? 'Start inspection' : 'Go to item' : 'Inspect faulty device',
    destination: asset ? `${asset.mapped && asset.tag ? asset.tag + ' · ' : ''}${asset.label || 'Selected item'}` : 'Choose equipment to begin' };
}
export function simpleFrameVisible({ selected, near, mapped, showContext = false }) {
  return Boolean(selected || (showContext && near && mapped));
}
export function mountInformationSettings({ document: doc = document, storage, onChange = () => {} } = {}) {
  if (storage === undefined) { try { storage = globalThis.localStorage; } catch { /* private browser */ } }
  const preference = createInformationPreference(storage), button = doc.getElementById('btn-settings'), dialog = doc.getElementById('information-settings');
  let context = false;
  const apply = () => {
    const simple = preference.mode === 'simple';
    doc.documentElement.dataset.informationMode = preference.mode;
    for (const radio of dialog.querySelectorAll('[name=information-mode]')) radio.checked = radio.value === preference.mode;
    doc.getElementById('simple-context-option').hidden = !simple;
    doc.getElementById('workspace-heading').textContent = simple ? 'Inspect equipment' : 'Explore the room';
    doc.getElementById('btn-safety-demo').textContent = simple ? 'Watch demo' : 'Play demo';
    doc.getElementById('btn-go-target').classList.toggle('primary', simple);
    doc.getElementById('btn-safety-demo').classList.toggle('primary', !simple);
    const map = doc.getElementById('btn-semantic-plan');
    map.lastChild.textContent = simple ? 'Map' : 'Floor plan';
    button.title = `${simple ? 'Simplified' : 'Extended'} information · change settings`;
    onChange({ mode: preference.mode, showContext: context });
  };
  const close = () => { dialog.close(); button.setAttribute('aria-expanded', 'false'); button.focus(); };
  button.addEventListener('click', () => {
    if (dialog.open) { close(); return; }
    dialog.showModal(); button.setAttribute('aria-expanded', 'true');
    dialog.querySelector('[name=information-mode]:checked').focus();
  });
  dialog.addEventListener('change', event => {
    if (event.target.name === 'information-mode') preference.set(event.target.value);
    else if (event.target.id === 'simple-show-context') context = event.target.checked;
    else return;
    apply();
  });
  doc.getElementById('btn-close-settings').addEventListener('click', close);
  dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  dialog.addEventListener('close', () => button.setAttribute('aria-expanded', 'false'));
  // Keep global movement / Take apart shortcuts from consuming dialog keystrokes.
  doc.defaultView?.addEventListener('keydown', event => {
    if (!dialog.open) return;
    event.stopImmediatePropagation();
    if (event.key === 'Escape') { event.preventDefault(); close(); }
  }, true);
  apply();
  return { get simple() { return preference.mode === 'simple'; }, get showContext() { return context; } };
}
