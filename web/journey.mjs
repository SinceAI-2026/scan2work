export function createJourney(route) {
  return {phase:"idle",route,elapsed:0,error:""};
}

function completed(journey) {
  return {journey:{...journey,phase:"arrived",elapsed:journey.route.duration_s,error:""},
    arrivedAssetId:journey.route.asset_id || null};
}

export function transitionJourney(journey,event) {
  const unchanged=()=>({journey,arrivedAssetId:null});
  switch(event.type) {
    case "start": {
      const route=event.route;
      if(!route?.waypoints?.length || !Number.isFinite(route.duration_s) || route.duration_s<0)
        throw new Error("A route needs waypoints and a valid duration.");
      const first=route.waypoints[0].position,last=route.waypoints.at(-1).position;
      const current=event.position;
      if(![current.x,current.z,...first,...last].every(Number.isFinite) || Math.hypot(first[0]-current.x,first[2]-current.z)>.001)
        throw new Error("A route must start at the robot's current position.");
      if(route.duration_s===0 && Math.hypot(last[0]-first[0],last[2]-first[2])>.001)
        throw new Error("A zero-duration route cannot move the robot.");
      const next={phase:"moving",route,elapsed:0,error:""};
      return route.duration_s===0 ? completed(next) : {journey:next,arrivedAssetId:null};
    }
    case "progress": {
      if(journey.phase!=="moving")return unchanged();
      if(!Number.isFinite(event.elapsed) || event.elapsed<journey.elapsed)
        throw new Error("Journey progress must move forward in time.");
      const next={...journey,elapsed:Math.min(event.elapsed,journey.route.duration_s)};
      return next.elapsed>=journey.route.duration_s ? completed(next) : {journey:next,arrivedAssetId:null};
    }
    case "pause":
      return journey.phase==="moving" ? {journey:{...journey,phase:"paused"},arrivedAssetId:null} : unchanged();
    case "resume":
      return journey.phase==="paused" ? {journey:{...journey,phase:"moving"},arrivedAssetId:null} : unchanged();
    case "block":
      return {journey:{...journey,phase:"blocked",error:event.message},arrivedAssetId:null};
    default:
      throw new Error(`Unknown journey event: ${event.type}`);
  }
}

export function journeyView(journey) {
  const controls={
    idle:{status:"READY TO EXPLORE",button:"Start",disabled:true},
    moving:{status:"NAVIGATING",button:"Pause",disabled:false},
    paused:{status:"PAUSED",button:"Continue",disabled:false},
    blocked:{status:"BLOCKED",button:"Choose destination",disabled:true},
    arrived:{status:"ARRIVED",button:"Arrived",disabled:true},
  };
  return {...controls[journey.phase],destination:journey.route.tag,error:journey.error,
    remainingSeconds:Math.max(0,Math.ceil(journey.route.duration_s-journey.elapsed))};
}
