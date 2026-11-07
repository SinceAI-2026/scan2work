import * as THREE from "three";
import { roomBimWalls, WALL_HEIGHT, WALL_THICKNESS, SLAB_THICKNESS } from "./bim-export.mjs";
import { overhead, major, groupOf } from "./semantic-plan.mjs";
import { bimCylinder, mergedVesselEnvelope } from "./bim-geometry.mjs";

const PALETTE = {
  valve:0x58a9db, machine:0x3cbaa2, vessel:0xe7a05a, instrument:0xb6a5dd,
  fixture:0x879da9, movable:0xaa7bd1, pipe:0x4daebb, other:0x78909b,
};
const UP = new THREE.Vector3(0,1,0);
const isPipe = asset => !!asset.cylinder || /pipe/.test(asset.class || "");

function disposeGroup(group) {
  group.traverse(object=>{
    object.geometry?.dispose();
    object.material?.dispose();
  });
  group.clear();
}

export function createBimRoom({camera, controls, container}) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b141b);
  scene.add(new THREE.HemisphereLight(0xe6f5ff,0x547077,2.1));
  const sun = new THREE.DirectionalLight(0xffffff,2.0);
  sun.position.set(4,8,5);
  scene.add(sun);

  const room = new THREE.Group();
  const equipment = new THREE.Group();
  const route = new THREE.Line(new THREE.BufferGeometry(),new THREE.LineBasicMaterial({color:0x8de6e9}));
  const robot = new THREE.Mesh(new THREE.CylinderGeometry(.105,.105,.028,24),new THREE.MeshBasicMaterial({color:0xa2eced}));
  scene.add(room,equipment,route,robot);

  const settings = {
    walls:document.getElementById("bim-walls"),
    pipes:document.getElementById("bim-pipes"),
    all:document.getElementById("bim-all"),
    plane:document.getElementById("bim-plane"),
  };
  const assets = new Map(), walls = [];
  const selection = document.getElementById("bim-selection");
  const center = new THREE.Vector3();
  let active = false, halfWidth = 0, halfDepth = 0, section, displayedAssets, selectedId, displayedRoute, savedDistance;

  function setRoom(items, sourceWalls, meta) {
    if (displayedAssets === items) return;
    displayedAssets = items;
    disposeGroup(room);
    disposeGroup(equipment);
    assets.clear();
    walls.length = 0;
    const bounds = roomBimWalls(sourceWalls,meta);
    const corners = bounds.flatMap(({from,to})=>[from,to]);
    const xs=corners.map(([x])=>x), zs=corners.map(([,z])=>z);
    const x0=Math.min(...xs), x1=Math.max(...xs), z0=Math.min(...zs), z1=Math.max(...zs);
    const width=x1-x0, depth=z1-z0;
    center.set((x0+x1)/2,.62,(z0+z1)/2);
    halfWidth=width/2;
    halfDepth=depth/2;

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
      room.add(wall);
      walls.push(wall);
    }

    const planeGeometry=new THREE.PlaneGeometry(width,depth);
    section=new THREE.Mesh(planeGeometry,new THREE.MeshBasicMaterial({
      color:0x79dbe4,transparent:true,opacity:.07,depthWrite:false,side:THREE.DoubleSide,
    }));
    section.rotation.x=-Math.PI/2;
    section.position.set(center.x,1.10,center.z);
    section.add(new THREE.LineSegments(new THREE.EdgesGeometry(planeGeometry),
      new THREE.LineBasicMaterial({color:0x79dbe4,transparent:true,opacity:.65})));
    room.add(section);

    for (const asset of items) {
      const cylinder=bimCylinder(asset);
      const envelope=mergedVesselEnvelope(asset,cylinder);
      const pipe=isPipe(asset);
      const colour=asset.is_target ? 0xee705e : PALETTE[pipe ? "pipe" : groupOf(asset)];
      const material=new THREE.MeshStandardMaterial({color:colour,roughness:.74,metalness:.05,
        transparent:envelope,opacity:envelope ? .18 : 1,depthWrite:!envelope});
      let mesh, mainPipe = false;
      if (cylinder?.radius>0) {
        const start=new THREE.Vector3().fromArray(cylinder.start), end=new THREE.Vector3().fromArray(cylinder.end);
        const vector=new THREE.Vector3().subVectors(end,start);
        mainPipe=pipe && !overhead(asset) && cylinder.radius>=.06 && vector.lengthSq()>=.85**2;
        if (vector.lengthSq()>0) {
          mesh=new THREE.Mesh(new THREE.CylinderGeometry(cylinder.radius,cylinder.radius,vector.length(),12),material);
          mesh.position.copy(start).lerp(end,.5);
          mesh.quaternion.setFromUnitVectors(UP,vector.normalize());
        }
      }
      if (!mesh) {
        mesh=new THREE.Mesh(new THREE.BoxGeometry(...asset.size),material);
        mesh.position.fromArray(asset.center);
        mesh.rotation.y=-asset.yaw;
      }
      const edge=new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry,30),
        new THREE.LineBasicMaterial({color:0xd7eef0,transparent:true,opacity:.23}));
      mesh.add(edge);
      mesh.userData.asset=asset;
      equipment.add(mesh);
      assets.set(asset.id,{asset,mesh,edge,pipe,mainPipe,envelope});
    }
    refreshLayers();
  }

  function refreshLayers() {
    if (section) section.visible=settings.plane.checked;
    for (const {asset,mesh,edge,pipe,mainPipe,envelope} of assets.values()) {
      const selected=asset.id===selectedId;
      mesh.visible=asset.is_target || selected || mainPipe ||
        (pipe || overhead(asset) ? settings.pipes.checked : major(asset) || settings.all.checked);
      edge.material.color.setHex(selected ? 0xc1ffff : asset.is_target ? 0xffad9d : 0xd7eef0);
      edge.material.opacity=selected || asset.is_target ? .95 : envelope ? .5 : .23;
      mesh.material.emissive.setHex(selected ? 0x17454c : 0x000000);
    }
  }

  function resetCamera() {
    const view=new THREE.Vector3(.92,.88,1.13).normalize();
    const right=new THREE.Vector3().crossVectors(UP,view).normalize();
    const vertical=new THREE.Vector3().crossVectors(view,right);
    const slope=Math.tan(THREE.MathUtils.degToRad(camera.fov/2));
    const aspect=container.clientWidth/Math.max(1,container.clientHeight);
    let distance=0;
    for(const x of [-halfWidth,halfWidth]) for(const y of [-center.y,WALL_HEIGHT-center.y]) for(const z of [-halfDepth,halfDepth]) {
      const corner=new THREE.Vector3(x,y,z);
      const framing=Math.max(Math.abs(corner.dot(right))/aspect,Math.abs(corner.dot(vertical)))/slope;
      distance=Math.max(distance,corner.dot(view)+framing);
    }
    camera.position.copy(center).addScaledVector(view,Math.min(36,distance*1.09));
    controls.target.copy(center);
    controls.update();
  }

  function setActive(next) {
    if (active===next) return;
    active=next;
    container.classList.toggle("bim-mode",active);
    document.body.classList.toggle("bim-view-open",active);
    document.getElementById("bim-options").hidden=!active;
    document.getElementById("btn-bim-back").hidden=!active;
    if (active) {
      savedDistance=controls.maxDistance;
      controls.maxDistance=36;
      resetCamera();
    } else controls.maxDistance=savedDistance;
  }

  function update(nav, journey, selected) {
    if (!active) return;
    if (selectedId!==selected?.id) {
      selectedId=selected?.id;
      selection.textContent=assets.has(selectedId) ? `${selected.tag} · ${selected.label}` : "";
      selection.hidden=!selection.textContent;
      refreshLayers();
    }
    for (const wall of walls) {
      const side=(wall.position.x-center.x)*(camera.position.x-center.x)+(wall.position.z-center.z)*(camera.position.z-center.z);
      wall.visible=settings.walls.checked && side<=0;
    }
    const waypoints=journey.phase!=="idle" && journey.phase!=="blocked" ? journey.route.waypoints : null;
    if (waypoints!==displayedRoute) {
      route.geometry.dispose();
      route.geometry = new THREE.BufferGeometry().setFromPoints((waypoints || []).map(({position:[x,,z]})=>new THREE.Vector3(x,.04,z)));
      displayedRoute=waypoints;
    }
    robot.position.set(nav.x,.035,nav.z);
  }

  for (const input of Object.values(settings)) input.addEventListener("change",refreshLayers);
  document.getElementById("btn-bim-reset").addEventListener("click",resetCamera);

  return {
    scene, setRoom, setActive, update, get active(){return active;},
    pick:raycaster=>raycaster.intersectObjects(equipment.children.filter(mesh=>mesh.visible),false)[0]?.object.userData.asset,
  };
}
