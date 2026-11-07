/** Display is a disclosure over the existing controls, never a second settings model. */
export function bindRoomControls({onShowAll,document:doc=document}) {
  const button=doc.getElementById("btn-display"),panel=doc.getElementById("layer-options"),host=doc.getElementById("display-switch");
  const close=({focus=false}={})=>{panel.hidden=true;button.setAttribute("aria-expanded","false");if(focus)button.focus();};
  const open=()=>{panel.hidden=false;button.setAttribute("aria-expanded","true");doc.getElementById("btn-close-display").focus();};
  button.addEventListener("click",()=>panel.hidden ? open() : close({focus:true}));
  doc.getElementById("btn-close-display").addEventListener("click",()=>close({focus:true}));
  doc.addEventListener("pointerdown",event=>{
    if(!panel.hidden && !host.contains(event.target))close({focus:panel.contains(doc.activeElement)});
  });
  doc.addEventListener("keydown",event=>{
    if(panel.hidden || event.key!=="Escape")return;
    event.preventDefault();event.stopPropagation();close({focus:true});
  },true);
  doc.getElementById("btn-semantic-plan").addEventListener("click",()=>close());
  doc.getElementById("btn-show-all").addEventListener("click",onShowAll);
  // Alternative equipment scopes; pipes remain independently selectable.
  for(const [id,other] of [["layer-all","layer-relevant"],["layer-relevant","layer-all"]]) {
    doc.getElementById(id).addEventListener("change",event=>{if(event.target.checked)doc.getElementById(other).checked=false;});
  }
  const overlay=doc.querySelector(".viewport-Controls-overlay"),container=doc.getElementById("canvas-container");
  if(typeof ResizeObserver!=="undefined")new ResizeObserver(()=>{
    container.style.setProperty("--room-controls-height",`${Math.ceil(overlay.getBoundingClientRect().height)}px`);
  }).observe(overlay);
  return {close};
}
