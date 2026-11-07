import { mapFrame, mapPoint } from "./game-map.mjs";
import { exportRoomIfc, roomBimWalls } from "./bim-export.mjs";
import { bimCylinder, mergedVesselEnvelope } from "./bim-geometry.mjs";
import { displayAssetTag } from "./asset-presentation.mjs";

import { overhead, major, groupOf, floorRegion, visualGeometry, regionVisible, layerSummary, layerSummaryText } from "./scene-layers.mjs";
export { overhead, major, groupOf } from "./scene-layers.mjs";

const NS = "http://www.w3.org/2000/svg";
const FILLS = {
  valve:"#b9d6f4", machine:"#a7ded2", vessel:"#f3ce9d", instrument:"#d7c8ef",
  fixture:"#d2dbdf", movable:"#eee5f4", other:"#d9e2e4",
};
const STATUS = {healthy:"#337458", watch:"#916814", degraded:"#b85221", attention_required:"#b85221"};

function svgNode(name, attributes={}, text) {
  const node = document.createElementNS(NS,name);
  for (const [key,value] of Object.entries(attributes)) node.setAttribute(key,value);
  if (text !== undefined) node.textContent=text;
  return node;
}

function footprint(asset) {
  const [x,,z]=asset.center, [sx,,sz]=asset.size;
  const c=Math.cos(asset.yaw || 0), s=Math.sin(asset.yaw || 0);
  return [[-1,-1],[1,-1],[1,1],[-1,1]].map(([u,v])=>[
    x+u*sx*c/2-v*sz*s/2, z+u*sx*s/2+v*sz*c/2,
  ]);
}

function floorPath(grid, meta, project) {
  const pieces=[];
  for (let z=0;z<meta.nz;z++) {
    let x=0;
    while (x<meta.nx) {
      if (!grid[x]?.[z]) {x++;continue;}
      const first=x++;
      while (grid[x]?.[z]) x++;
      const [left,top]=project(meta.x_min+first*meta.res,meta.z_min+(z+1)*meta.res);
      const [right,bottom]=project(meta.x_min+x*meta.res,meta.z_min+z*meta.res);
      pieces.push(`M${left} ${top}h${right-left}v${bottom-top}H${left}Z`);
    }
  }
  return pieces.join("");
}

function addPlanLabels(svg, items, project, frame, select) {
  const occupied=[];
  const ordered=items.filter(asset=>asset.mapped || asset.is_target)
    .sort((a,b)=>Number(b.is_target)-Number(a.is_target)||a.tag.localeCompare(b.tag));
  for (const asset of ordered) {
    const [x,y]=project(asset.center[0],asset.center[2]);
    const w=asset.tag.length*7+16, h=20;
    let spot;
    for (const radius of [16,31,48,65]) {
      for (const [dx,dy] of [[1,-1],[1,1],[-1,-1],[-1,1],[0,-1],[0,1],[1,0],[-1,0]]) {
        const left=x+dx*radius-w/2, top=y+dy*radius-h/2, right=left+w, bottom=top+h;
        if (left<frame.x+5 || right>frame.x+frame.width-5 || top<frame.y+5 || bottom>frame.y+frame.height-5) continue;
        if (occupied.some(p=>left<p.right+3 && right>p.left-3 && top<p.bottom+3 && bottom>p.top-3)) continue;
        spot={left,top,right,bottom};
        break;
      }
      if (spot) break;
    }
    if (!spot) continue;
    occupied.push(spot);
    const cx=spot.left+w/2, cy=spot.top+h/2;
    const color=asset.is_target ? "#ad3127" : "#385052";
    const label=svgNode("g",{"data-plan-label":asset.id,class:"semantic-plan-tag"});
    label.append(svgNode("line",{x1:x,y1:y,x2:cx,y2:cy,stroke:color,"stroke-width":.9}),
      svgNode("rect",{x:spot.left,y:spot.top,width:w,height:h,rx:4,fill:"#fffdf5",stroke:color}),
      svgNode("text",{x:cx,y:cy+4,fill:color,"text-anchor":"middle","font-size":12,"font-weight":700},asset.tag));
    label.addEventListener("click",()=>select(asset));
    svg.append(label);
  }
}

export function bindSemanticPlan({snapshot, inspect, navigate, viewModel}) {
  const dialog=document.getElementById("semantic-plan");
  const svg=document.getElementById("semantic-plan-drawing");
  const search=document.getElementById("semantic-plan-search");
  const results=document.getElementById("semantic-plan-results");
  const showAll=document.getElementById("semantic-plan-all");
  const showOverhead=document.getElementById("semantic-plan-overhead");
  const showFloor=document.getElementById("semantic-plan-floor");
  const showPipes=document.getElementById("semantic-plan-pipes");
  const counts=document.getElementById("semantic-plan-counts");
  const details=document.getElementById("semantic-plan-selection");
  const go=document.getElementById("semantic-plan-navigate");
  const openAsset=document.getElementById("semantic-plan-inspect");
  let current, selected, project, routeLine, robot;
  let lastRoute, lastPhase;

  function select(asset) {
    selected=asset;
    details.textContent=`${displayAssetTag(asset)} · ${asset.label || asset.class}${asset.source_region_id ? " · unreviewed" : ""}`;
    go.disabled=false;
    openAsset.disabled=false;
    const node=[...svg.querySelectorAll("[data-plan-asset]")].find(item=>item.dataset.planAsset===asset.id);
    if (!node) draw();
    else {
      svg.querySelector("[aria-pressed=true]")?.setAttribute("aria-pressed","false");
      node.setAttribute("aria-pressed","true");
    }
  }

  function searchAssets() {
    results.replaceChildren();
    const term=search.value.trim().toLowerCase();
    if (!term) return;
    const matches=current.assets.filter(asset=>[asset.id,asset.tag,asset.asset_id,asset.label,asset.class_label]
      .some(value=>String(value ?? "").toLowerCase().includes(term)));
    for (const asset of matches.slice(0,7)) {
      const button=document.createElement("button");
      button.type="button";
      button.textContent=`${displayAssetTag(asset)} · ${asset.label || asset.class}`;
      button.addEventListener("click",()=>select(asset));
      results.append(button);
    }
    if (!matches.length) results.textContent="No equipment matches.";
  }

  function draw() {
    const meta=current.navigation.grid_meta;
    const imported=current.assets.some(asset=>asset.source_region_id);
    const floorAvailable=Boolean(current.derived_context?.observed_floor?.cells_xz?.length || current.assets.some(floorRegion));
    const contextAvailable=Boolean(current.derived_context?.pipe_segments?.length);
    showFloor.parentElement.hidden=!floorAvailable;
    showPipes.parentElement.hidden=!contextAvailable;
    for(const el of dialog.querySelectorAll("[data-floor-legend]"))el.hidden=!floorAvailable;
    for(const el of dialog.querySelectorAll("[data-unreviewed-legend]"))el.hidden=!imported;
    showAll.parentElement.lastChild.textContent=imported ? " Include small regions" : " Show all floor objects";
    showOverhead.parentElement.lastChild.textContent=imported ? " Include overhead regions" : " Show overhead objects";
    const width=900, height=width*meta.nz/meta.nx, frame=mapFrame(width,height);
    project=(x,z)=>mapPoint(meta,width,height,x,z);
    svg.setAttribute("viewBox",`0 0 ${width} ${height}`);
    svg.replaceChildren();
    svg.append(svgNode("rect",{...frame,fill:"#faf8ef"}));
    const observed=current.derived_context?.observed_floor;
    if (showFloor.checked && observed?.cells_xz?.length) {
      const res=observed.res || observed.cell_size || meta.res;
      let path="";
      for(const [x,z] of observed.cells_xz){
        const [left,top]=project(x-res/2,z+res/2),[right,bottom]=project(x+res/2,z-res/2);
        path+=`M${left} ${top}h${right-left}v${bottom-top}H${left}Z`;
      }
      svg.append(svgNode("path",{d:path,fill:"#e2dfca","data-layer":"observed-floor"}));
    }
    if(current.navigation.source_floor_grid)svg.append(svgNode("path",{d:floorPath(current.navigation.source_floor_grid,meta,project),fill:"#edf1ed","data-layer":"original-floor-grid"}));
    svg.append(svgNode("path",{d:floorPath(current.navigation.walkable_grid,meta,project),fill:"#dcebe5","data-layer":"walkable-grid"}));
    const walls=roomBimWalls(current.walls,meta);
    for (const {from,to} of walls) {
      const [x1,y1]=project(...from),[x2,y2]=project(...to);
      svg.append(svgNode("line",{x1,y1,x2,y2,stroke:"#23383e","stroke-width":8,"stroke-linecap":"square"}));
    }

    const options={all:showAll.checked,raised:showOverhead.checked,floors:showFloor.checked,selectedId:selected?.id};
    const assets=current.assets.filter(asset=>regionVisible(asset,options)).map(visualGeometry);
    counts.textContent=layerSummaryText(layerSummary(current.assets,options),imported ? "regions" : "objects",imported ? "small" : "other");
    const pipes=current.derived_context?.pipe_segments || [];
    const fittedTotal=current.assets.filter(asset=>bimCylinder(asset)).length,fittedVisible=assets.filter(asset=>bimCylinder(asset)).length;
    document.getElementById("semantic-plan-context").textContent=imported
      ? `${walls.length} assumed wall guides · ${showPipes.checked ? pipes.length : 0} / ${pipes.length} fitted pipe segments · context is not additional extracted objects`
      : `${fittedVisible} / ${fittedTotal} fitted object cylinders shown · ${walls.length} wall guides`;
    document.getElementById("semantic-plan-evidence-note").textContent=imported
      ? "Observed floor is scan evidence, not a claim that every floor point is walkable. Grey envelopes have unverified labels and boundaries."
      : "Original Wael object geometry and walkable simulation grid. Dimensions remain unverified.";
    document.getElementById("semantic-plan-export-note").textContent=contextAvailable
      ? "Approximate IFC includes region envelopes, unreviewed pipe fits, and assumed walls/slab. Floor sample counts are included as evidence, not a reconstructed surface."
      : "Approximate IFC includes fitted object cylinders, equipment envelopes, and assumed walls/slab.";
    if (showPipes.checked) for(const pipe of pipes) {
      if(!pipe.start || !pipe.end) continue;
      const [x1,y1]=project(pipe.start[0],pipe.start[2]),[x2,y2]=project(pipe.end[0],pipe.end[2]);
      const node=svgNode("line",{x1,y1,x2,y2,stroke:"#558d95","stroke-width":Math.max(1.5,2*(pipe.radius || .02)*frame.width/(meta.nx*meta.res)),opacity:.5,"pointer-events":"none"});
      svg.append(node);
    }
    assets.sort((a,b)=>Number(overhead(b))-Number(overhead(a)) ||
      Number(a.mapped)-Number(b.mapped)||b.size[0]*b.size[2]-a.size[0]*a.size[2]);
    const scale=frame.width/(meta.nx*meta.res);
    for (const asset of assets) {
      const [x,y]=project(asset.center[0],asset.center[2]);
      const raised=overhead(asset);
      const stroke=asset.is_target ? "#ad3127" : (asset.mapped ? STATUS[asset.condition_state] : null) || "#577078";
      const node=svgNode("g",{class:"semantic-plan-asset",tabindex:0,role:"button","data-plan-asset":asset.id,
        "aria-label":`${displayAssetTag(asset)} · ${asset.label || asset.class}`,"aria-pressed":String(selected?.id===asset.id)});
      const group=groupOf(asset), cylinder=bimCylinder(asset);
      const envelope=mergedVesselEnvelope(asset,cylinder);
      const fill=raised ? "none" : floorRegion(asset) ? "#d6d0ae" : FILLS[group];
      const attrs={class:"semantic-plan-shape",fill,stroke,"stroke-width":asset.is_target ? 3 : 1.6,
        "fill-opacity":asset.source_region_id ? (floorRegion(asset) ? .25 : .08) : envelope ? .25 : 1,
        "stroke-dasharray":raised || envelope ? "5 4" : group==="movable" ? "3 2" : "none"};
      if (cylinder?.radius>0) {
        const [x1,y1]=project(cylinder.start[0],cylinder.start[2]), [x2,y2]=project(cylinder.end[0],cylinder.end[2]);
        if (Math.hypot(x2-x1,y2-y1)<2) {
          node.append(svgNode("circle",{...attrs,cx:x1,cy:y1,r:cylinder.radius*scale}));
        } else {
          node.append(svgNode("line",{...attrs,x1,y1,x2,y2,"stroke-width":raised ? 2 : 2*cylinder.radius*scale}));
        }
      } else {
        const points=footprint(asset).map(p=>project(...p).join(",")).join(" ");
        node.append(svgNode("polygon",{...attrs,points}));
      }
      node.append(svgNode("circle",{cx:x,cy:y,r:8,fill:"transparent"}));
      if (asset.is_target) node.append(svgNode("circle",{cx:x,cy:y,r:19,fill:"none",stroke,"stroke-dasharray":"3 3","pointer-events":"none"}));
      node.addEventListener("click",()=>select(asset));
      node.addEventListener("keydown",event=>{
        if (event.key==="Enter" || event.key===" ") {event.preventDefault();select(asset);}
      });
      svg.append(node);
    }
    routeLine=svgNode("polyline",{fill:"none",stroke:"#247384","stroke-width":4,"stroke-linecap":"round",
      "stroke-linejoin":"round","pointer-events":"none"});
    svg.append(routeLine);
    addPlanLabels(svg,assets,project,frame,select);
    robot=svgNode("circle",{r:7,fill:"#176978",stroke:"#fffdf5","stroke-width":2,"pointer-events":"none"});
    svg.append(robot);
    const y=frame.y+frame.height+22;
    svg.append(svgNode("line",{x1:frame.x,y1:y,x2:frame.x+scale,y2:y,stroke:"#23383e","stroke-width":2}),
      svgNode("text",{x:frame.x+scale+8,y:y+4,"font-size":13},current.scaleUnverified || current.navigation.scale_verified === false ? "1 scan unit (unverified)" : "1 m"),
      svgNode("text",{x:frame.x+frame.width,y:y+4,"text-anchor":"end","font-size":13},"+Z ↑"));
    lastRoute=null;
    update(current.robot,current.journey);
  }

  function update(position, journey) {
    if (!dialog.open || !project) return;
    const [x,y]=project(position.x,position.z);
    robot.setAttribute("cx",x);
    robot.setAttribute("cy",y);
    if (lastRoute!==journey.route || lastPhase!==journey.phase) {
      const route=journey.route.kind==="idle" || journey.phase==="blocked" ? [] : journey.route.waypoints;
      routeLine.setAttribute("points",route.map(({position:[x,,z]})=>project(x,z).join(",")).join(" "));
      lastRoute=journey.route;
      lastPhase=journey.phase;
    }
  }

  document.getElementById("btn-semantic-plan").addEventListener("click",()=>{
    current=snapshot();
    // Opened from the Aerial view: start from its Show in room choices (pipes = overhead objects).
    showAll.checked=!!document.getElementById("layer-all")?.checked;
    showOverhead.checked=!!document.getElementById("layer-pipes")?.checked;
    selected=current.assets.find(asset=>asset.id===current.selected?.id) || current.assets.find(asset=>asset.is_target);
    dialog.showModal();
    draw();
    if (selected) select(selected);
    searchAssets();
  });
  document.getElementById("semantic-plan-close").addEventListener("click",()=>dialog.close());
  document.getElementById("semantic-plan-model").addEventListener("click",()=>{dialog.close();viewModel();});
  search.addEventListener("input",searchAssets);
  showAll.addEventListener("change",draw);
  showOverhead.addEventListener("change",draw);
  showFloor.addEventListener("change",draw);
  showPipes.addEventListener("change",draw);
  openAsset.addEventListener("click",()=>{dialog.close();inspect(selected);});
  go.addEventListener("click",()=>{dialog.close();navigate(selected);});
  document.getElementById("semantic-plan-export").addEventListener("click",()=>{
    const room=snapshot();
    const meta=room.scaleUnverified ? {...room.navigation.grid_meta,units:"unverified source units"} : room.navigation.grid_meta;
    const data=exportRoomIfc(room.assets,room.walls,meta,new Date(),room.derived_context);
    const url=URL.createObjectURL(new Blob([data],{type:"application/step"}));
    const link=document.createElement("a");
    link.href=url;
    link.download="scan2work-equipment-room.ifc";
    link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  return {update};
}
