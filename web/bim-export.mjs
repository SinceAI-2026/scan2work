// IFC4 STEP export of the live room model. The scan is Y-up; IFC uses Z-up.
import { bimCylinder, mergedVesselEnvelope } from "./bim-geometry.mjs";
const IFC_TYPES = {
  control_valve: "IFCVALVE", circulation_pump: "IFCPUMP", electric_motor: "IFCELECTRICMOTOR",
  pressure_sensor: "IFCSENSOR", vibration_sensor: "IFCSENSOR", expansion_vessel: "IFCTANK",
  electrical_cabinet: "IFCELECTRICDISTRIBUTIONBOARD", heat_exchanger: "IFCHEATEXCHANGER",
  pipe_fitting: "IFCPIPEFITTING", structural_column: "IFCCOLUMN", sink: "IFCSANITARYTERMINAL",
  vertical_pipe: "IFCPIPESEGMENT", horizontal_pipe: "IFCPIPESEGMENT", overhead_pipe: "IFCPIPESEGMENT",
};
export const WALL_HEIGHT = 2.4;
export const WALL_THICKNESS = .15;
export const SLAB_THICKNESS = .12;

const ifcXYZ = ([x,y,z]) => [x,-z,y];
const real = value => value.toFixed(8).replace(/0+$/, "");
const tuple = items => `(${items.join(",")})`;
const quoted = value => "'" + String(value ?? "").replace(/'/g, "''").replace(/[^\x20-\x7e]+|\\/g, text =>
  "\\X2\\" + text.split("").map(char => char.charCodeAt(0).toString(16).padStart(4,"0")).join("").toUpperCase() + "\\X0\\") + "'";

function guid(key) {
  const alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$";
  let bits = 0x6c62272e07bb014262b821756295c58dn;
  for (const char of `field-atlas/equipment-room/${key}`) {
    bits = BigInt.asUintN(128, (bits ^ BigInt(char.codePointAt(0))) * 0x1000000000000000000013bn);
  }
  let code = "";
  for (let i=0;i<22;i++,bits>>=6n) code = alphabet[Number(bits & 63n)] + code;
  return quoted(code);
}

export function roomBimWalls(walls, meta) {
  if (walls?.length) return walls;
  const x0=meta.x_min, z0=meta.z_min, x1=x0+meta.nx*meta.res, z1=z0+meta.nz*meta.res;
  const ring=[[x0,z0],[x1,z0],[x1,z1],[x0,z1]];
  return ring.map((from,i)=>({name:`Wall ${i+1}`,from,to:ring[(i+1)%ring.length]}));
}

export function exportRoomIfc(assets, walls, meta, timestamp = new Date()) {
  const unverified = assets.some(asset => asset.source_region_id) || /unverified/i.test(meta.units || "");
  const rows = [];
  const add = (entity,...args) => {
    const ref = `#${rows.length+1}`;
    rows.push(`${ref}=${entity}${tuple(args)};`);
    return ref;
  };
  const point = xyz => add("IFCCARTESIANPOINT",tuple(xyz.map(real)));
  const direction = xyz => add("IFCDIRECTION",tuple(xyz.map(real)));
  const up = direction([0,0,1]);
  const east = direction([1,0,0]);
  const origin = add("IFCAXIS2PLACEMENT3D",point([0,0,0]),up,east);
  const place = (parent,xyz=[0,0,0],axis=up,ref=east) =>
    add("IFCLOCALPLACEMENT",parent,add("IFCAXIS2PLACEMENT3D",point(xyz),axis,ref));
  const model = add("IFCGEOMETRICREPRESENTATIONCONTEXT","$","'Model'",3,"0.00001",origin,"$");
  const body = add("IFCGEOMETRICREPRESENTATIONSUBCONTEXT","'Body'","'Model'","*","*","*","*",model,"$",".MODEL_VIEW.","$");
  const units = add("IFCUNITASSIGNMENT",tuple([add("IFCSIUNIT","*",".LENGTHUNIT.","$",".METRE.")]));
  const project = add("IFCPROJECT",guid("project"),"$","'Field Atlas'",unverified ? quoted("Approximate envelopes only. Physical scale unverified; IFC metres are an assumed unit convention, not measured dimensions.") : "$","$","$","$",tuple([model]),units);
  const sitePlace=place("$"), buildingPlace=place(sitePlace), storeyPlace=place(buildingPlace), roomPlace=place(storeyPlace);
  const site=add("IFCSITE",guid("site"),"$","'Valmet'","$","$",sitePlace,"$","$",".ELEMENT.","$","$","$","$","$");
  const building=add("IFCBUILDING",guid("building"),"$","'Equipment room building'","$","$",buildingPlace,"$","$",".ELEMENT.","$","$","$");
  const storey=add("IFCBUILDINGSTOREY",guid("storey"),"$","'Ground level'","$","$",storeyPlace,"$","$",".ELEMENT.","0.");
  const room=add("IFCSPACE",guid("room"),"$","'Equipment Room'","$","$",roomPlace,"$","$",".ELEMENT.",".INTERNAL.","$");
  for (const [key,parent,child] of [["project-site",project,site],["site-building",site,building],
    ["building-storey",building,storey],["storey-room",storey,room]]) {
    add("IFCRELAGGREGATES",guid(key),"$","$","$",parent,tuple([child]));
  }
  const profile = (width,depth) => add("IFCRECTANGLEPROFILEDEF",".AREA.","$","$",real(width),real(depth));
  function shape(section, depth) {
    const solid=add("IFCEXTRUDEDAREASOLID",section,origin,up,real(depth));
    const representation=add("IFCSHAPEREPRESENTATION",body,"'Body'","'SweptSolid'",tuple([solid]));
    return add("IFCPRODUCTDEFINITIONSHAPE","$","$",tuple([representation]));
  }
  function box(parent,xyz,angle,width,depth,height) {
    return [place(parent,xyz,up,direction([Math.cos(angle),Math.sin(angle),0])),
      shape(profile(width,depth),height)];
  }

  const shell = [];
  const boundaries=roomBimWalls(walls,meta);
  const corners=boundaries.flatMap(wall=>[wall.from,wall.to]);
  const xs=corners.map(([x])=>x), zs=corners.map(([,z])=>z);
  const x0=Math.min(...xs), x1=Math.max(...xs), z0=Math.min(...zs), z1=Math.max(...zs);
  const slab=box(storeyPlace,[(x0+x1)/2,-(z0+z1)/2,-SLAB_THICKNESS],0,x1-x0,z1-z0,SLAB_THICKNESS);
  shell.push(add("IFCSLAB",guid("slab"),"$","'Room floor'","'Plan envelope with assumed thickness'","$",...slab,"$",".BASESLAB."));
  boundaries.forEach(({name,from:[x,z],to:[endX,endZ]},i)=>{
    const geometry=box(storeyPlace,[(x+endX)/2,-(z+endZ)/2,0],Math.atan2(z-endZ,endX-x),
      Math.hypot(endX-x,endZ-z),WALL_THICKNESS,WALL_HEIGHT);
    shell.push(add("IFCWALL",guid(`wall/${i}`),"$",quoted(name),"'Plan envelope with assumed height and thickness'","$",...geometry,"$",".NOTDEFINED."));
  });

  const products = [];
  for (const asset of assets) {
    const {id,tag,label,center,size} = asset;
    const cylinder=bimCylinder(asset);
    const [x,y,z]=center, [sx,sy,sz]=size;
    let geometry, method;
    if (cylinder?.radius>0) {
      const start=ifcXYZ(cylinder.start), end=ifcXYZ(cylinder.end);
      const delta=end.map((value,i)=>value-start[i]);
      const length=Math.hypot(...delta);
      if (length>0) {
        const axis=delta.map(value=>value/length);
        const ref=Math.abs(axis[0])<.99 ? [0,axis[2],-axis[1]] : [axis[2],0,-axis[0]];
        const section=add("IFCCIRCLEPROFILEDEF",".AREA.","$","$",real(cylinder.radius));
        geometry=[place(roomPlace,start,direction(axis),direction(ref)),shape(section,length)];
        method=cylinder.source;
      }
    }
    if (!geometry) {
      geometry=box(roomPlace,ifcXYZ([x,y-sy/2,z]),-(asset.yaw || 0),sx,sz,sy);
      method=mergedVesselEnvelope(asset,cylinder) ? "Merged scan envelope" : "Scan-derived oriented envelope";
    }
    const item=add(IFC_TYPES[asset.class] || "IFCBUILDINGELEMENTPROXY",guid(`object/${id}`),"$",
      quoted(label || tag || id),quoted(method),"$",...geometry,quoted(tag || id),".NOTDEFINED.");
    products.push(item);
    const values = [
      ["ObjectId",id],["AssetId",asset.asset_id],["AssetTag",tag],["Category",asset.class_label || asset.class],
      ["LabelSource",asset.label_source],["GeometrySource",method],
    ];
    if (unverified) values.push(["ScaleStatus","Unverified; source unit assumed as one metre for IFC exchange"],
      ["GeometryStatus","Approximate envelope; not a measured BIM object"], ["ReviewStatus",asset.review_status || "unreviewed"]);
    if (asset.mapped) values.push(["Condition",asset.condition_state],["Registry","Valmet Excel builder pack"]);
    const properties=values.filter(([,value])=>value).map(([name,value])=>
      add("IFCPROPERTYSINGLEVALUE",quoted(name),"$",`IFCLABEL(${quoted(value)})`,"$"));
    const set=add("IFCPROPERTYSET",guid(`properties/${id}`),"$","'FieldAtlas_Asset'","$",tuple(properties));
    add("IFCRELDEFINESBYPROPERTIES",guid(`asset-properties/${id}`),"$","$","$",tuple([item]),set);
  }
  add("IFCRELCONTAINEDINSPATIALSTRUCTURE",guid("shell"),"$","$","$",tuple(shell),storey);
  if (products.length) add("IFCRELCONTAINEDINSPATIALSTRUCTURE",guid("equipment"),"$","$","$",tuple(products),room);
  return [
    "ISO-10303-21;","HEADER;",unverified ? "FILE_DESCRIPTION(('Approximate equipment room; scale unverified'),'2;1');" : "FILE_DESCRIPTION(('Scan-derived equipment room'),'2;1');",
    `FILE_NAME('field-atlas-equipment-room.ifc','${timestamp.toISOString().slice(0,19)}',('Field Atlas'),('Valmet x Since AI'),'Field Atlas','Field Atlas','');`,
    "FILE_SCHEMA(('IFC4'));","ENDSEC;","DATA;",...rows,"ENDSEC;","END-ISO-10303-21;","",
  ].join("\n");
}
