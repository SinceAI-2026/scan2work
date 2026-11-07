import * as THREE from "three";
import { roomBimWalls, WALL_HEIGHT, WALL_THICKNESS, SLAB_THICKNESS } from "./bim-export.mjs";
import { filterShows, wallsShown, onFilterChange } from "./view-filter.mjs";
import { bimCylinder, mergedVesselEnvelope } from "./bim-geometry.mjs";
import { explodeOffset } from "./explode.mjs";
import { classHue } from "./asset-presentation.mjs";
import { visualGeometry } from "./scene-layers.mjs";

const UP = new THREE.Vector3(0,1,0);

function disposeGroup(group) {
  group.traverse(object=>{
    object.geometry?.dispose();
    object.material?.dispose();
  });
  group.clear();
}

// The BIM model is a layer of the main view, not a scene of its own: the same camera, robot, route, frames,
// labels and panels. The Show bar's "BIM model" swaps the splat for the floor and equipment (setActive); the
// walls follow the "Show in room" Walls box in every layer of the Aerial view (setWallsAllowed).
export function createBimRoom({scene, camera}) {
  const root = new THREE.Group();
  root.add(new THREE.HemisphereLight(0xe6f5ff,0x547077,2.1));
  const sun = new THREE.DirectionalLight(0xffffff,2.0);
  sun.position.set(4,8,5);
  root.add(sun);

  const room = new THREE.Group();        // slab and grid: BIM layer only
  const wallGroup = new THREE.Group();   // walls: any layer, when the Walls box is ticked
  const equipment = new THREE.Group();   // BIM layer only, filtered by the Show in room boxes
  room.visible = equipment.visible = wallGroup.visible = false;
  root.add(room,wallGroup,equipment);
  scene.add(root);

  const assets = new Map(), walls = [];
  const center = new THREE.Vector3();
  let active = false, wallsAllowed = false, displayedAssets, selectedId;

  function setRoom(items, sourceWalls, meta) {
    if (displayedAssets === items) return;
    displayedAssets = items;
    disposeGroup(room);
    disposeGroup(wallGroup);
    disposeGroup(equipment);
    assets.clear();
    walls.length = 0;
    const bounds = roomBimWalls(sourceWalls,meta);
    const corners = bounds.flatMap(({from,to})=>[from,to]);
    const xs=corners.map(([x])=>x), zs=corners.map(([,z])=>z);
    const x0=Math.min(...xs), x1=Math.max(...xs), z0=Math.min(...zs), z1=Math.max(...zs);
    const width=x1-x0, depth=z1-z0;
    center.set((x0+x1)/2,.62,(z0+z1)/2);

    const slab = new THREE.Mesh(new THREE.BoxGeometry(width,SLAB_THICKNESS,depth),
      new THREE.MeshStandardMaterial({color:0x273b42,roughness:.92}));
    slab.position.set(center.x,-SLAB_THICKNESS/2,center.z);
    room.add(slab);
    const grid = [];
    for(let x=Math.ceil(x0);x<x1;x++) grid.push(x,.006,z0,x,.006,z1);
    for(let z=Math.ceil(z0);z<z1;z++) grid.push(x0,.006,z,x1,.006,z);
    const gridGeometry = new THREE.BufferGeometry().setAttribute("position",new THREE.Float32BufferAttribute(grid,3));
    room.add(new THREE.LineSegments(gridGeometry,new THREE.LineBasicMaterial({color:0x44616a,opacity:.42,transparent:true})));

    for (const {from:[x,z],to:[endX,endZ]} of bounds) {
      const wallGeometry = new THREE.BoxGeometry(Math.hypot(endX-x,endZ-z),WALL_HEIGHT,WALL_THICKNESS);
      const wall = new THREE.Mesh(wallGeometry,new THREE.MeshBasicMaterial({
        color:0xe5aa60,transparent:true,opacity:.21,depthWrite:false,
      }));
      wall.position.set((x+endX)/2,WALL_HEIGHT/2,(z+endZ)/2);
      wall.rotation.y=-Math.atan2(endZ-z,endX-x);
      wall.add(new THREE.LineSegments(new THREE.EdgesGeometry(wallGeometry),
        new THREE.LineBasicMaterial({color:0xe5aa60,transparent:true,opacity:.52,depthWrite:false})));
      wallGroup.add(wall);
      walls.push(wall);
    }


    for (const asset of items) {
      const cylinder=bimCylinder(asset);
      const envelope=mergedVesselEnvelope(asset,cylinder);
      const unreviewed=Boolean(asset.source_region_id && asset.label_verified!==true);
      const geometry=visualGeometry(asset);
      const colour=unreviewed ? 0x97a9b0 : asset.is_target ? 0xee705e : classHue(String(asset.class || "").toLowerCase());
      const material=new THREE.MeshStandardMaterial({color:colour,roughness:.74,metalness:.05,
        transparent:envelope||unreviewed,opacity:unreviewed ? .12 : envelope ? .18 : 1,depthWrite:!envelope&&!unreviewed});
      let mesh;
      if (cylinder?.radius>0) {
        const start=new THREE.Vector3().fromArray(cylinder.start), end=new THREE.Vector3().fromArray(cylinder.end);
        const vector=new THREE.Vector3().subVectors(end,start);
        if (vector.lengthSq()>0) {
          mesh=new THREE.Mesh(new THREE.CylinderGeometry(cylinder.radius,cylinder.radius,vector.length(),12),material);
          mesh.position.copy(start).lerp(end,.5);
          mesh.quaternion.setFromUnitVectors(UP,vector.normalize());
        }
      }
      if (!mesh) {
        mesh=new THREE.Mesh(new THREE.BoxGeometry(...geometry.size),material);
        mesh.position.fromArray(geometry.center);
        mesh.rotation.y=-(geometry.yaw||0);
      }
      const edge=new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry,30),
        new THREE.LineBasicMaterial({color:0xd7eef0,transparent:true,opacity:.23}));
      mesh.add(edge);
      mesh.userData.asset=asset;
      mesh.userData.base=mesh.position.clone();   // explode moves the mesh from here
      equipment.add(mesh);
      assets.set(asset.id,{asset,mesh,edge,envelope});
    }
    refreshLayers();
  }

  function refreshLayers() {
    for (const {asset,mesh,edge,envelope} of assets.values()) {
      const selected=asset.id===selectedId;
      mesh.visible=asset.is_target || selected || filterShows(asset);
      edge.material.color.setHex(selected ? 0xc1ffff : asset.is_target ? 0xffad9d : 0xd7eef0);
      edge.material.opacity=selected || asset.is_target ? .95 : envelope ? .5 : .23;
      mesh.material.emissive.setHex(selected ? 0x17454c : 0x000000);
    }
  }

  function setActive(next) {
    active=next;
    room.visible=equipment.visible=active;
  }

  function setWallsAllowed(next) { wallsAllowed=next; }

  function update(selected) {
    wallGroup.visible=wallsAllowed && wallsShown();
    if (wallGroup.visible) for (const wall of walls) {   // only the far walls, so they never block the view
      const side=(wall.position.x-center.x)*(camera.position.x-center.x)+(wall.position.z-center.z)*(camera.position.z-center.z);
      wall.visible=side<=0;
    }
    if (!active) return;
    if (selectedId!==selected?.id) {
      selectedId=selected?.id;
      refreshLayers();
    }
    for (const {asset,mesh} of assets.values()) mesh.position.copy(mesh.userData.base).add(explodeOffset(asset));
  }

  onFilterChange(refreshLayers);

  return { setRoom, setActive, setWallsAllowed, update, get active(){return active;} };
}
