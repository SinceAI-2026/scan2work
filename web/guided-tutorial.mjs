export const TUTORIAL_KEY = 'scan2work:tutorial:v1';
export function createTutorialPreference(storage) {
  let saved={seen:false,autoShow:true};
  try { const value=JSON.parse(storage?.getItem(TUTORIAL_KEY)||'null'); if(value) saved={seen:value.seen===true,autoShow:value.autoShow!==false}; } catch {}
  const write=()=>{try{storage?.setItem(TUTORIAL_KEY,JSON.stringify(saved));}catch{}};
  return {get shouldShow(){return saved.autoShow&&!saved.seen;},dismiss(never=false){saved.seen=true;if(never)saved.autoShow=false;write();},offerNextVisit(enabled){saved={seen:!enabled,autoShow:!!enabled};write();}};
}
export function tutorialSuppressed(url) {
  const u=new URL(url,'https://scan2work.invalid/');
  return u.searchParams.get('tutorial')==='0'||/^#(?:clone|apart)=/.test(u.hash);
}
export const TUTORIAL_STEPS = [
  {id:'equipment',target:'#inspection-queue, #asset-list',title:'Find the right equipment',text:'Choose a device from To inspect or use the equipment search. Selecting it highlights the object and opens its details. The tutorial points out controls without clicking them for you.',area:'sidebar'},
  {id:'navigate',target:'#btn-go-target',title:'Get to the selected device',text:'Start inspection follows a walkable route to the selected device in Simplified mode. Extended mode offers Inspect faulty device for the scenario target. It starts movement, not a completed service record.',area:'sidebar'},
  {id:'tour',target:'#btn-safety-demo',title:'Follow an inspection tour',text:'Inspection tour moves the technician to the faulty device, then explains energy hazards and evidence. You can pause or stop it. It does not complete inspection checks or change device readings.',area:'sidebar'},
  {id:'camera',target:'#view-fab',title:'Choose your view',text:'Use the camera menu for an aerial overview or to follow the technician. In the room, drag to orbit, right-drag to pan and scroll to zoom.',area:'room'},
  {id:'map',target:'#btn-semantic-plan',title:'See the room from above',text:'Map opens the floor plan so you can find equipment and inspect the available routes. A scan surface is not automatically a safe walking path.',area:'room'},
  {id:'details',target:'#device-status-summary',title:'Read the condition first',text:'Equipment details show the selected device’s condition and available workbook measurements. Missing or unmapped data stays unknown. Metrics & trends opens the recorded signals.',area:'inspector'},
  {id:'service',target:'#field-job, #active-asset-details',title:'Record the work you actually do',text:'The inspection checklist records your confirmations. Safety steps must be confirmed before dependent work. Completing this tutorial does not start or complete a job.',area:'inspector'},
  {id:'diagnostics',target:'.copilot-block',title:'Check the evidence and next steps',text:'Service guidance connects the device condition to workbook evidence and troubleshooting instructions. Expand the evidence and diagnostic details to review the sources before acting.',area:'inspector'},
  {id:'parts',target:'#btn-take-apart',title:'Inspect the parts',text:'Take apart opens the available component model. Use it to understand the assembly; a visualization does not verify isolation or authorize physical work.',area:'inspector'},
  {id:'library',target:'a[href="library.html"]',title:'Reuse an object from the library',text:'Asset library holds saved scan objects. Clone and place creates a preview in the room: move it, then Place or Cancel. Copies have no borrowed device readings. You can move or delete a placed copy.',area:'room'},
  {id:'settings',target:'#btn-settings',title:'More tools when you need them',text:'Settings switches between Simplified and Extended. Extended reveals scan layers, framing, Take apart and room editing. You can replay this tutorial here at any time. When you’re ready, choose Inspection tour to see the technician follow the route.',area:'room'},
];
export function visibleTutorialTarget(doc,step) {
  return step.target.split(',').flatMap(selector=>[...doc.querySelectorAll(selector.trim())]).find(el=>{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&!el.closest('[hidden]')&&getComputedStyle(el).visibility!=='hidden';})||null;
}
export function mountGuidedTutorial({host,document:doc=document,storage,url=location.href}={}) {
  if(storage===undefined){try{storage=globalThis.localStorage;}catch{}}
  const pref=createTutorialPreference(storage), dialog=doc.createElement('dialog');
  dialog.id='guided-tutorial';dialog.className='guided-tutorial';dialog.setAttribute('aria-labelledby','tutorial-title');dialog.setAttribute('aria-describedby','tutorial-description');dialog.dataset.preserveDemo='';
  dialog.innerHTML=`<div class="tutorial-shade"></div><div class="tutorial-ring" aria-hidden="true"></div><section class="tutorial-card"><button type="button" class="tutorial-close" aria-label="Close tutorial">×</button><small id="tutorial-progress"></small><h2 id="tutorial-title" aria-live="polite"></h2><p id="tutorial-description"></p><label class="tutorial-never"><input type="checkbox" id="tutorial-never"> Don’t show this welcome again</label><div class="tutorial-actions"><button type="button" data-tutorial="skip">Skip tutorial</button><button type="button" data-tutorial="back">Back</button><button type="button" data-tutorial="next">Next</button></div></section>`;
  doc.body.append(dialog);
  const card=dialog.querySelector('.tutorial-card'),ring=dialog.querySelector('.tutorial-ring'), title=dialog.querySelector('h2'), description=dialog.querySelector('#tutorial-description'), progress=dialog.querySelector('#tutorial-progress'), never=dialog.querySelector('.tutorial-never');
  const back=dialog.querySelector('[data-tutorial=back]'),next=dialog.querySelector('[data-tutorial=next]'),skip=dialog.querySelector('[data-tutorial=skip]');
  const settings=doc.getElementById('information-settings'), replay=doc.getElementById('btn-replay-tutorial'),auto=doc.getElementById('tutorial-auto-show');
  let active=false,index=-1,steps=[],target=null,previousFocus=null,restore=null,frame=0;
  const sync=()=>{auto.checked=pref.shouldShow;};sync();
  const position=()=>{
    if(!active)return;
    const w=doc.defaultView.innerWidth,h=doc.defaultView.innerHeight,margin=12;
    if(index<0||!target){ring.hidden=true;card.style.left=`${Math.max(margin,(w-card.offsetWidth)/2)}px`;card.style.top=`${Math.max(margin,(h-card.offsetHeight)/2)}px`;return;}
    const r=target.getBoundingClientRect(),left=Math.max(4,r.left-5),top=Math.max(4,r.top-5),right=Math.min(w-4,r.right+5),bottom=Math.min(h-4,r.bottom+5);
    ring.hidden=right<=left||bottom<=top;
    Object.assign(ring.style,{left:left+'px',top:top+'px',width:Math.max(0,right-left)+'px',height:Math.max(0,bottom-top)+'px'});
    const cw=card.offsetWidth,ch=card.offsetHeight;
    let x=right+16,y=top;
    if(x+cw>w-margin){x=left-cw-16;if(x<margin){x=Math.min(left,w-cw-margin);y=bottom+16;if(y+ch>h-margin)y=top-ch-16;}}
    Object.assign(card.style,{left:Math.max(margin,Math.min(x,w-cw-margin))+'px',top:Math.max(margin,Math.min(y,h-ch-margin))+'px'});
  };
  const schedule=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(position);};
  function finish(){if(!active)return;pref.dismiss(dialog.querySelector('#tutorial-never').checked);sync();active=false;dialog.close();restore?.();restore=null;host?.exit?.();doc.body.classList.remove('tutorial-active');previousFocus?.focus?.({preventScroll:true});}
  function showStep(i){
    index=i;const step=steps[index];host?.reveal?.(step.area);target=visibleTutorialTarget(doc,step);
    if(!target){steps.splice(index,1);if(index>=steps.length){finish();return;}return showStep(index);}
    target.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});
    progress.textContent=`${index+1} of ${steps.length}`;title.textContent=step.title;description.textContent=step.text;never.hidden=true;
    back.hidden=false;back.disabled=index===0;skip.textContent='Skip tutorial';next.textContent=index===steps.length-1?'Finish':'Next';
    dialog.classList.remove('tutorial-welcome');position();next.focus({preventScroll:true});schedule();
  }
  function collectSteps(){return TUTORIAL_STEPS.filter(step=>{host?.reveal?.(step.area);return visibleTutorialTarget(doc,step);});}
  function begin(welcome=false){
    if(active)return;
    if(host?.canStart&&!host.canStart()){replay.textContent='Finish the current room action first';return;}
    previousFocus=settings.open?doc.getElementById('btn-settings'):doc.activeElement;if(settings.open)settings.close();restore=host?.enter?.();active=true;doc.body.classList.add('tutorial-active');dialog.showModal();
    dialog.querySelector('#tutorial-never').checked=false;
    steps=welcome?[]:collectSteps();
    host?.reveal?.('room');
    if(!welcome){if(steps.length)showStep(0);else finish();return;}
    index=-1;target=null;dialog.classList.add('tutorial-welcome');progress.textContent='WELCOME TO SCAN2WORK';title.textContent='Find the machine. Know the next step.';description.textContent='Take a short tour of the room controls, inspection workflow and asset library. Nothing will move or be marked complete during this tutorial.';never.hidden=false;back.hidden=true;skip.textContent='Explore on my own';next.textContent='Show tutorial';position();next.focus({preventScroll:true});schedule();
  }
  next.addEventListener('click',()=>{if(index<0){steps=collectSteps();pref.dismiss(dialog.querySelector('#tutorial-never').checked);sync();if(steps.length)showStep(0);else finish();}else if(index===steps.length-1)finish();else showStep(index+1);});back.addEventListener('click',()=>showStep(index-1));skip.addEventListener('click',finish);dialog.querySelector('.tutorial-close').addEventListener('click',finish);
  dialog.addEventListener('cancel',e=>{e.preventDefault();finish();});
  doc.defaultView.addEventListener('keydown',e=>{if(!active)return;e.stopImmediatePropagation();if(e.key==='Escape'){e.preventDefault();finish();}else if(e.key==='Tab'){const buttons=[...card.querySelectorAll('button,input')].filter(b=>!b.disabled&&!b.hidden&&!b.closest('[hidden]'));const first=buttons[0],last=buttons.at(-1);if(e.shiftKey&&doc.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&doc.activeElement===last){e.preventDefault();first.focus();}}},true);
  // The real controls stay inert while the modal explains them. Resize/scroll only repositions the spotlight.
  doc.defaultView.addEventListener('resize',schedule);doc.addEventListener('scroll',schedule,true);
  replay.addEventListener('click',()=>begin());auto.addEventListener('change',()=>{pref.offerNextVisit(auto.checked);sync();});
  return {get active(){return active;},ready(){if(pref.shouldShow&&!tutorialSuppressed(url))begin(true);},start:()=>begin(),close:finish};
}
