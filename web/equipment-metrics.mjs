/** Workbook metrics for the selected canonical device.
 * Signal naming and the dark signal-card presentation draw on Rasmus's XRHub
 * (SinceAI-2026/rasse, 5a3ca02). Uses this viewer's bindings, never peer/generated data.
 */
import {escapeHtml as esc} from './imported-assets.mjs';

const SIGNALS={
  process_pressure_bar:['Pressure','bar'],process_flow_pct:['Flow','%'],
  position_actual_pct:['Actual position','%'],position_setpoint_pct:['Position setpoint','%'],
  position_error_pct:['Position error','%'],pneumatic_supply_bar:['Supply pressure','bar'],
  stiction_index:['Stiction index','index'],load_for_opening_pct:['Opening load','%'],
  vibration_mm_s:['Vibration','mm/s'],bearing_temp_c:['Bearing temperature','°C'],
  motor_current_a:['Motor current','A'],speed_rpm:['Speed','rpm'],
  signal_quality_pct:['Signal quality','%'],sensor_bias:['Sensor bias','unit unspecified'],
};
const finite=Number.isFinite;
export const metricNumber=v=>finite(v)?new Intl.NumberFormat('en-GB',{maximumFractionDigits:3}).format(v):'Not supplied';
const nameOf=key=>SIGNALS[key]?.[0]||key.replaceAll('_',' ');
const unitOf=key=>SIGNALS[key]?.[1]||'';

export function metricsFor(asset,context,{removed=false,knownScenario=false}={}) {
  if(asset?.mapped!==true||asset.movable||['movable','not_instrumented'].includes(asset.condition_state)||removed)return null;
  const record=context?.devices?.[asset.id];
  if(record&&record.asset_id!==asset.asset_id)return null;
  const base={id:asset.id,assetId:asset.asset_id,tag:asset.tag||asset.asset_id,label:asset.label||asset.class_label||'',
    condition:String(asset.condition_state||'unknown').replaceAll('_',' '),health:finite(asset.health_score)?asset.health_score:null,
    quality:record?.data_quality||asset.data_quality_issues||[],metrics:[],snapshot:record?.snapshot_at||null,scenario:false};
  const scenario=asset.simulated_emergency===true||record?.simulated_emergency===true||record?.provenance?.kind==='runtime_scenario';
  if(context?.unavailable&&(scenario||knownScenario))return {...base,health:null,unavailable:true,scenario:true};
  if(scenario){
    const m=record?.primary_metric;
    const current=m?[{key:m.signal,label:m.label||nameOf(m.signal),unit:m.unit||'',latest:finite(m.latest)?m.latest:null}]:Object.entries(asset.telemetry?.latest||{}).map(([key,value])=>({key,label:nameOf(key),unit:unitOf(key),latest:finite(value)?value:null}));
    return {...base,health:null,scenario:true,snapshot:null,metrics:current.map(v=>({...v,values:[],timestamps:[],range:null,mean:null}))};
  }
  const telemetry=asset.telemetry||{},series=telemetry.series||{},timestamps=Array.isArray(series.timestamps)?series.timestamps:[];
  const keys=[...new Set([...Object.keys(telemetry.latest||{}),...Object.keys(series).filter(k=>k!=='timestamps')])];
  const metrics=keys.map(key=>{
    const latest=telemetry.latest?.[key],stats=telemetry.stats?.[key]||{};
    const values=Array.isArray(series[key])?series[key].map(v=>finite(v)?v:null):[];
    const historical=[...values.filter(finite),stats.min,stats.max].filter(finite);
    const range=historical.length?{min:Math.min(...historical),max:Math.max(...historical)}:null;
    return {key,label:nameOf(key),unit:unitOf(key),latest:finite(latest)?latest:null,values,timestamps,range,mean:finite(stats.mean)?stats.mean:null};
  });
  return {...base,contextUnavailable:!context||context.unavailable,snapshot:base.snapshot||timestamps.at(-1)||null,metrics};
}

/** Keep missing samples as gaps. Only the valve setpoint shares the actual-position axis. */
export function metricChartSvg(model,key,{width=760}={}) {
  const m=model.metrics.find(x=>x.key===key);if(!m)return '';
  if(!m.values.some(finite))return '<p class="metrics-empty">No recorded trend supplied for this signal.</p>';
  const comparison=key==='position_actual_pct'?model.metrics.find(x=>x.key==='position_setpoint_pct'&&x.unit===m.unit):null;
  const values=[...m.values,...(comparison?.values||[])].filter(finite);
  const lo=Math.min(...values),hi=Math.max(...values),pad=hi===lo?Math.max(Math.abs(lo)*.05,.1):(hi-lo)*.1,min=lo-pad,max=hi+pad;
  const W=Math.max(280,width),H=205,L=54,R=12,T=15,B=29,n=Math.max(m.values.length,comparison?.values.length||0,2);
  const x=i=>L+i/(n-1)*(W-L-R),y=v=>T+(max-v)/(max-min)*(H-T-B);
  const line=(arr,color)=>{let path='',pen=false;arr.forEach((v,i)=>{if(!finite(v)){pen=false;return;}path+=`${pen?'L':'M'}${x(i).toFixed(2)},${y(v).toFixed(2)} `;pen=true;});return `<path d="${path}" fill="none" stroke="${color}" stroke-width="2.5"/>`;};
  const grid=[0,.5,1].map(f=>{const value=min+f*(max-min),Y=y(value);return `<line x1="${L}" x2="${W-R}" y1="${Y}" y2="${Y}" stroke="#51425e"/><text x="${L-8}" y="${Y+4}" text-anchor="end">${esc(metricNumber(value))}</text>`;}).join('');
  const first=m.timestamps[0]||'First sample',last=m.timestamps[m.values.length-1]||'Last sample',tick=t=>String(t).match(/\d\d:\d\d/)?.[0]||t;
  return `<svg class="metrics-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(m.label)} recorded trend${comparison?' with position setpoint':''}. ${m.values.filter(finite).length} supplied samples; gaps are missing values.">${grid}${line(m.values,'#ef8dc6')}${comparison?line(comparison.values,'#84d9df'):''}<text x="${L}" y="${H-5}">${esc(tick(first))}</text><text x="${W-R}" y="${H-5}" text-anchor="end">${esc(tick(last))}</text></svg><p>Recorded period: ${esc(first)} – ${esc(last)}</p>${comparison?'<p class="metrics-legend"><span>● Actual position</span><span>● Position setpoint</span></p>':''}`;
}

function gauge(m) {
  if(m.latest===null)return '<span class="metrics-gauge-empty">Reading not supplied</span>';
  if(!m.range)return '<span class="metrics-gauge-empty">Range not supplied</span>';
  const {min,max}=m.range;
  if(max===min)return '<span class="metrics-gauge-empty">Constant recorded range</span>';
  const fraction=Math.max(0,Math.min(1,(m.latest-min)/(max-min)));
  return `<svg class="metrics-gauge" viewBox="0 0 120 66" aria-hidden="true"><path d="M10 60 A50 50 0 0 1 110 60" pathLength="100" fill="none" stroke="#4a3b56" stroke-width="8"/><path d="M10 60 A50 50 0 0 1 110 60" pathLength="100" fill="none" stroke="#ef8dc6" stroke-width="8" stroke-dasharray="${fraction*100} 100"/></svg>`;
}

export function mountEquipmentMetrics({button,document:doc=globalThis.document}) {
  const dialog=doc.createElement('dialog');dialog.className='equipment-metrics';dialog.setAttribute('aria-labelledby','equipment-metrics-title');dialog.setAttribute('data-preserve-demo','');doc.body.append(dialog);
  let model=null,selected='',opener=null;const scenarioIds=new Set();
  button.hidden=true;button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-expanded','false');
  function close(){if(dialog.open)dialog.close();}
  function render(){
    if(!model)return;
    if(!model.metrics.some(m=>m.key===selected))selected=model.metrics.find(m=>m.key==='position_actual_pct')?.key||model.metrics[0]?.key||'';
    const m=model.metrics.find(m=>m.key===selected);
    const chartWidth=Math.min(760,(dialog.clientWidth||doc.defaultView.innerWidth||920)-80);
    dialog.innerHTML=`<header><div><small>METRICS & TRENDS</small><h2 id="equipment-metrics-title">${esc(model.tag)} · ${esc(model.label)}</h2></div><button type="button" data-metrics-close aria-label="Close metrics">×</button></header>
      <div class="metrics-status"><span>${esc(model.condition)}</span>${model.health===null?'':`<span>Workbook health <b>${esc(metricNumber(model.health))}/100</b></span>`}</div>
      <p class="metrics-provenance">${model.unavailable?'Scenario context unavailable · readings withheld until refreshed.':model.scenario?'Scripted demo scenario · not a sensor reading. Archived history is not shown as scenario history.':`Synthetic workbook snapshot · ${esc(model.snapshot||'Time not supplied')} · not live telemetry${model.contextUnavailable?' · current scenario status unavailable; loaded snapshot only':''}`}</p>
      ${model.unavailable?'':`<div class="metrics-cards" aria-label="Supplied device signals">${model.metrics.map(v=>`<button type="button" class="metrics-signal" data-signal="${esc(v.key)}" aria-pressed="${v.key===selected}"><span>${esc(v.label)}</span>${gauge(v)}<strong>${esc(metricNumber(v.latest))}${v.latest===null?'':` <small>${esc(v.unit)}</small>`}</strong><small>${v.range?`Observed ${esc(metricNumber(v.range.min))}–${esc(metricNumber(v.range.max))} ${esc(v.unit)}`:'Historical range not supplied'}</small></button>`).join('')}</div>
      ${m?`<section class="metrics-trend"><div class="metrics-trend-heading"><h3>${esc(m.label)} <small>${esc(m.unit)}</small></h3><label>Signal <select aria-label="Trend signal">${model.metrics.map(v=>`<option value="${esc(v.key)}" ${v.key===selected?'selected':''}>${esc(v.label)}</option>`).join('')}</select></label></div>${metricChartSvg(model,selected,{width:chartWidth})}<p>${m.mean===null?'':`Workbook mean: ${esc(metricNumber(m.mean))} ${esc(m.unit)} · `}${m.values.filter(finite).length} supplied samples${m.values.some(v=>v===null)?' · missing samples appear as gaps':''}</p></section>`:'<p class="metrics-empty">No readings supplied for this device.</p>'}`}
      <footer>${model.scenario?'Scenario readings have no workbook timestamp.':'Source: Valmet synthetic builder pack · Telemetry_Sample_3d · '+esc(model.assetId)}<br>Gauge arcs show the workbook recorded range, not safe operating limits. Stored workbook ranges and means may cover more samples than the displayed trend.${model.quality.length?`<details><summary>${model.quality.length} data-quality flag${model.quality.length===1?'':'s'}</summary><ul>${model.quality.map(q=>`<li>${esc(typeof q==='string'?q:[q.issue_id,q.issue_type||q.type,q.description||q.detail,q.severity,q.source_sheet,q.recommended_handling].filter(Boolean).join(' · '))}</li>`).join('')}</ul></details>`:''}</footer>`;
  }
  button.addEventListener('click',()=>{if(!model)return;opener=button;render();dialog.showModal();render();button.setAttribute('aria-expanded','true');dialog.querySelector('[data-metrics-close]').focus();});
  dialog.addEventListener('click',event=>{
    if(event.target===dialog||event.target.closest('[data-metrics-close]')){close();return;}
    const signal=event.target.closest('[data-signal]')?.dataset.signal;
    if(signal){selected=signal;render();dialog.querySelector('[data-signal][aria-pressed=true]')?.focus();}
  });
  dialog.addEventListener('change',event=>{if(event.target.matches('select')){selected=event.target.value;render();dialog.querySelector('select').focus();}});
  dialog.addEventListener('close',()=>{button.setAttribute('aria-expanded','false');if(!button.hidden)opener?.focus();});
  // Consume Escape before room-level handlers close the inspector or change the camera.
  doc.defaultView.addEventListener('keydown',event=>{if(!dialog.open||event.key!=='Escape')return;event.preventDefault();event.stopImmediatePropagation();close();},true);
  return {close,get isOpen(){return dialog.open;},update(asset,context,options){
    const next=metricsFor(asset,context,{...options,knownScenario:scenarioIds.has(asset?.id)}),changed=model?.id!==next?.id;
    if(next?.scenario)scenarioIds.add(next.id);else if(context?.devices?.[asset?.id])scenarioIds.delete(asset.id);
    model=next;button.hidden=!model;if(changed)selected='';
    if(!model)close();else if(dialog.open){render();dialog.querySelector('[data-metrics-close]').focus();}
  }};
}
