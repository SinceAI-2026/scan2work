import {escapeHtml as esc} from './imported-assets.mjs';
const number=value=>Number.isFinite(value)?Number(value.toFixed(2)).toString():null;
export function summaryFor(asset,context) {
  if(!asset?.mapped)return null;
  if(context?.unavailable)return {unavailable:true,condition:'unknown',issue:'Equipment context unavailable',reading:null,next:'Refresh the context before using service guidance.',quality:0};
  const record=context?.devices?.[asset.id];
  if(record && record.asset_id!==asset.asset_id)return null;
  const metric=record?.primary_metric;
  const value=number(metric?.latest),comparison=number(metric?.comparison?.value);
  const reading=value===null?null:`${metric.label}: ${value}${metric.unit?' '+metric.unit:''}${comparison===null?'':` · ${metric.comparison.label}: ${comparison}${metric.unit?' '+metric.unit:''}`}`;
  return {scenario:record?.simulated_emergency===true||record?.provenance?.kind==='runtime_scenario',condition:String(asset.condition_state||'unknown').replaceAll('_',' '),issue:asset.issue_summary||asset.issue_code||'No issue recorded',reading,
    snapshot:record?.snapshot_at||metric?.as_of||asset.telemetry?.series?.timestamps?.at(-1)||'Time not supplied',
    next:record?.next_check||asset.recommended_next_check||'Review the available evidence',
    sources:record?.source_ids||[],quality:record?.data_quality?.length||0};
}
export function renderDeviceSummary(container,asset,context) {
  const data=summaryFor(asset,context);container.hidden=!data;if(!data){container.replaceChildren();return;}
  container.innerHTML=`<div class="device-summary-top"><strong>${esc(data.issue)}</strong><span>${esc(data.condition)}</span></div>${data.reading?`<p class="device-summary-reading">${esc(data.reading)}</p>`:''}<small>${data.unavailable?'Current readings could not be refreshed':data.scenario?'Scripted demo scenario · not a sensor reading':`Synthetic workbook snapshot · ${esc(data.snapshot)}`}</small><p><b>Next check:</b> ${esc(data.next)}</p>${data.quality?`<small>${data.quality} data-quality flag${data.quality===1?'':'s'} · verify the evidence</small>`:''}`;
}
