/** Reuse host update notifications, never add another polling loop. */
export function createContextRefresh({load,apply}) {
  let request=0;
  return async function refresh(){
    const current=++request;
    apply({unavailable:true,devices:{}});
    try {
      const context=await load();
      if(!context?.devices)throw new Error('Equipment context unavailable');
      if(current===request)apply(context);
    } catch {
      if(current===request)apply({unavailable:true,devices:{}});
    }
  };
}
/** Update only matching mapped status fields; geometry, ownership and workbook bindings stay untouched. */
export function applyContextStatus(assets,context) {
  for(const asset of assets){
    const record=context?.devices?.[asset.id];
    if(!asset.mapped||!record||record.asset_id!==asset.asset_id)continue;
    for(const key of ['condition_state','health_score','issue_code','issue_summary'])if(key in record)asset[key]=record[key];
    if(record.next_check)asset.recommended_next_check=record.next_check;
  }
}
