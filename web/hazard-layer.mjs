/** Selected/nearby energy overlay, adapted from Rasse hazardFx.ts (5a3ca02).
 * Canonical host geometry only. Visual illustrations never modify navigation or establish safety clearance.
 */
export function visibleEnergy(context,assets,selected,position,max=3) {
  return assets.filter(a=>a.mapped&&context?.devices?.[a.id]?.hazards?.length&&context.devices[a.id].asset_id===a.asset_id)
    .map(asset=>({asset,record:context.devices[asset.id],distance:Math.hypot(asset.center[0]-position.x,asset.center[2]-position.z)}))
    .filter(x=>x.asset.id===selected?.id||x.distance<2)
    .sort((a,b)=>Number(b.asset.id===selected?.id)-Number(a.asset.id===selected?.id)||a.distance-b.distance).slice(0,max);
}
export function energyDescription(record) {
  const hazard=record?.hazards?.find(h=>Number.isFinite(h.reading?.value));
  const reading=hazard?`${hazard.label||hazard.kind}: ${Number(hazard.reading.value.toFixed(2))}${hazard.reading.unit?' '+hazard.reading.unit:''}`:'No measured energy reading available';
  const scenario=record?.simulated_emergency===true||record?.provenance?.kind==='runtime_scenario';
  return {reading,source:scenario?'Scripted demo scenario · not a live sensor reading':`Synthetic workbook snapshot · ${hazard?.reading?.as_of||record?.snapshot_at||'time not supplied'}`};
}
export function createHazardLayer(THREE,{scene,context,assets,selected,position,container,button}) {
  const root=new THREE.Group();root.name='Illustrative energy zones';scene.add(root);
  const note=document.createElement('aside');note.className='hazard-summary';note.hidden=true;note.setAttribute('aria-label','Potential energy');container.append(note);
  let enabled=false,demo=false,key='';
  const material=color=>new THREE.LineBasicMaterial({color,depthWrite:false});
  const line=(points,color)=>{const object=new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points.map(p=>new THREE.Vector3(...p))),material(color));object.renderOrder=7;root.add(object);};
  function clear(){for(const object of [...root.children]){object.geometry?.dispose();object.material?.dispose();root.remove(object);}}
  function draw(zone,danger){
    const [x,,z]=zone.asset.center, radius=Math.max(.35,Math.max(zone.asset.size[0],zone.asset.size[2])/2+(danger?.5:.18));
    const circle=[],curtain=[],tape=[];const n=56,point=(i,y)=>[x+Math.cos(i/n*Math.PI*2)*radius,y,z+Math.sin(i/n*Math.PI*2)*radius];
    for(let i=0;i<n;i++){
      circle.push(point(i,.025),point(i+1,.025));
      if(danger){curtain.push(point(i,.025),point(i,1.55));for(const y of [.75,1.48]){
        const a=point(i,y),b=point(i+1,y),c=point(i+1,y+.07),d=point(i,y+.07);const geometry=new THREE.BufferGeometry().setAttribute('position',new THREE.Float32BufferAttribute([...a,...b,...c,...a,...c,...d],3));
        const mesh=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({color:i%2?0x1b2521:0xffcf38,side:THREE.DoubleSide}));mesh.renderOrder=7;root.add(mesh);
      }}
    }
    line(circle,danger?0xef5d47:0xf1b74b);
    if(danger){line(curtain,0xf1c642);for(let d=-radius;d<radius;d+=.18){const span=Math.sqrt(radius*radius-d*d);tape.push([x+d,.03,z-span],[x+d,.03,z+span]);}line(tape,0xdb553b);}
  }
  function frame(){
    root.visible=enabled;note.hidden=!enabled||demo;
    const list=enabled?visibleEnergy(typeof context==='function'?context():context,assets(),selected(),position()).filter(x=>!demo||x.asset.id===selected()?.id):[];
    const next=JSON.stringify([enabled,demo,list.map(x=>[x.asset.id,x.asset.center,x.asset.size,x.record.hazards,x.record.snapshot_at,x.record.provenance])]);
    if(key===next)return;key=next;clear();for(const zone of list)draw(zone,demo&&zone.asset.id===selected()?.id);
    const focus=list.find(x=>x.asset.id===selected()?.id)||list[0];
    note.replaceChildren();const heading=document.createElement('strong'),body=document.createElement('p'),small=document.createElement('small');
    heading.textContent=focus?`${focus.asset.tag} · potential energy`:'Potential energy';
    const description=energyDescription(focus?.record);
    body.textContent=focus?description.reading:'Select workbook-linked equipment to inspect its energy context.';
    small.textContent=`${description.source}. Illustrative zones, unverified scale. No live isolation confirmation.`;
    note.append(heading,body,small);
  }
  function setEnabled(on){enabled=Boolean(on);button.setAttribute('aria-pressed',String(enabled));key='';frame();}
  button.addEventListener('click',()=>setEnabled(!enabled));
  return {frame,setEnabled,get enabled(){return enabled;},setDemo(on){demo=Boolean(on);key='';frame();}};
}
