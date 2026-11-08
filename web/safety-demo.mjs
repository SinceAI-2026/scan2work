/** An interruptible, browser-local story inspired by rasse/maintenance/web/demo.ts (5a3ca02).
 * Host capabilities contain visualization/navigation only. No jobs, sensors, LLM or persistence API.
 */
export function createSafetyStory(host) {
  let phase='idle',paused=false,time=0;
  const enter=next=>{phase=next;time=0;if(next==='hazard')host.hazard(true);if(next==='evidence'){host.hazard(false);host.evidence();}host.changed?.(snapshot());};
  const snapshot=()=>({phase,paused});
  function stop({takeover=false}={}){if(phase==='idle')return;host.pause();host.hazard(false);host.restore({takeover});phase='idle';paused=false;host.changed?.(snapshot());}
  return {get state(){return snapshot();},start(){if(phase!=='idle')stop();host.capture();paused=false;enter('route');host.navigate();},
    tick(dt){if(phase==='idle'||paused)return;const travel=host.travel();if(phase==='route'){if(travel==='blocked'){stop();host.failed?.();}else if(travel==='arrived'){host.focus();enter('hazard');}return;}time+=Math.max(0,dt);if(phase==='hazard'&&time>=7)enter('evidence');else if(phase==='evidence'&&time>=9)enter('complete');},
    next(){if(phase==='hazard')enter('evidence');else if(phase==='evidence')enter('complete');else if(phase==='complete')stop();},
    togglePause(){if(phase==='idle'||phase==='complete')return;paused=!paused;paused?host.pause():host.resume();host.changed?.(snapshot());},stop};
}
export function mountSafetyDemo({host,button,container,document:doc=document}) {
  const panel=doc.createElement('section');panel.className='safety-demo-card';panel.hidden=true;panel.setAttribute('aria-label','Inspection tour');
  panel.innerHTML='<small>INSPECTION TOUR · SIMULATION</small><h3></h3><p></p><div><button type="button" data-demo="pause">Pause tour</button><button type="button" data-demo="next">Next</button><button type="button" data-demo="stop">Stop tour</button></div>';
  container.append(panel);
  const text={route:['1 · Find the faulty device','The technician follows the existing walkable route. Use any room control to take over.'],hazard:['2 · Make hidden energy visible','Illustration: pressure can remain after shutdown. This archive cannot confirm isolation; the zone is not a measured exclusion distance.'],evidence:['3 · Check the source, then decide','Review the original photo, workbook reading and next check in Equipment details. Ilkka and the inspection checklist remain available.'],complete:['Tour complete','No inspection checks, jobs or Arduino readings were changed. Continue exploring or start an inspection yourself.']};
  const story=createSafetyStory({...host,changed:s=>{panel.hidden=s.phase==='idle';container.classList.toggle('safety-demo-active',!panel.hidden);doc.body?.classList.toggle('guided-demo-active',!panel.hidden);button.setAttribute('aria-pressed',String(!panel.hidden));if(panel.hidden)return;panel.querySelector('h3').textContent=text[s.phase][0];panel.querySelector('p').textContent=host.caption?.(s.phase)||text[s.phase][1];panel.querySelector('[data-demo=pause]').textContent=s.paused?'Resume tour':'Pause tour';panel.querySelector('[data-demo=pause]').hidden=s.phase==='complete';panel.querySelector('[data-demo=next]').disabled=s.phase==='route';panel.querySelector('[data-demo=next]').textContent=s.phase==='complete'?'Done':'Next';}});
  button.addEventListener('click',()=>story.state.phase==='idle'?story.start():story.stop());
  panel.addEventListener('click',event=>{const action=event.target.closest('[data-demo]')?.dataset.demo;if(action==='pause')story.togglePause();if(action==='next')story.next();if(action==='stop')story.stop();});
  const takeover=event=>{if(story.state.phase==='idle'||event.target.closest?.('[data-preserve-demo]'))return;if(event.key==='Escape'){story.stop();return;}if(panel.contains(event.target)||button.contains(event.target))return;if(event.type==='pointerdown'&&event.target.closest?.('button,a,input,select,summary'))return;story.stop({takeover:true});};
  for(const event of ['pointerdown','click','wheel','keydown'])doc.addEventListener(event,takeover,{capture:true,passive:true});
  return story;
}
