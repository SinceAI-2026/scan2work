import * as THREE from "three";
import { inspectionCameraPose } from "./inspection-camera.mjs";
import { assetPresentation } from "./asset-presentation.mjs";
import { renderAssetPhoto } from "./asset-photo.mjs";
import { blockBox, planObstacleDrop } from "./temporary-obstacles.mjs";
import { resolveRenderQuality } from "./render-quality.mjs";
import { createJourney, transitionJourney, journeyView } from "./journey.mjs";
import { manualObstacles, manualStep } from "./manual-walk.mjs";
import { withWalkthroughPassages } from "./walkthrough-navigation.mjs";
import { indexRoomScan, fillRoomObject, traceRoomInstance, reviewedTrolleyParts } from "./room-fill.mjs";
import { loadRoomPlacements, saveRoomPlacements, placedRoomAsset, roomBoundsOverlap, roomPlacementError, copyPlacedSplats } from "./room-placement.mjs";
import { navigationAfterRoomEdits } from "./room-edit-navigation.mjs";
import { roomEditStore } from "./room-edits.mjs";
import { createGameMap, mapFrame, mapPoint, mapWorldPoint } from "./game-map.mjs";
import { bindSemanticPlan } from "./semantic-plan.mjs";
import { createBimRoom } from "./bim-room.mjs";
import { fitBimVessels } from "./bim-geometry.mjs";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { explodeState, explodeOffset, loadInstanceUniforms, applyExplode, mountExplodeControls, SHADER_DECL, SHADER_OFFSET, SHADER_COLOR } from "./explode.mjs";
import { createTakeApart } from "./take-apart.mjs";
import { sampleRoute, nearbyAssets, planFromPosition, reachableFloor, floorDestination, assetDestination, planRoomTour, advanceAlongRoute, segmentIsFree } from "./navigation-view.mjs";

const state = {
  twin: null,
  assetsById: new Map(),
  assetsByObj: new Map(),
  selectedAsset: null,
  journey: null,
  manualWalk: null,
  walkSummary: null,
  previousRoute: null,
  demoObstacle: null,
  speed: 1.0,
  splatMode: "ewa",        // "ewa" | "semantic"
  cameraPreset: "walk",
  boxesMode: "proximity",  // "proximity" | "all" | "off"
  visualReviewFilter: "mapped",
  classFilter: "all",
  searchQuery: "",
};

let scene, camera, renderer, controls;
let maxRenderDimension;
let splatMesh, splatMaterial, sortWorker;
let sortBusy = false;
let lastSortCamKey = "";
let hudLastUpdate = -1;
let proximityKey = "";
let mapBackground;
let semanticPlan;
let bimView;

let currentNavigation;
let copilotRequestId=0;
let pathLineGroup, robotGroup, lidarRing, proxRing;
let previousRouteLine;
const boxMeshes = new Map();
const hudElements = new Map();
const imageCache = new Map();
const attract = {enabled:new URLSearchParams(location.search).get("attract") !== "0",active:false,lastActivity:0};
const walkKeys = new Set();
const movementKeys = ["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"];
let equipmentObstacles;
let takeApart = null; // one object alone, the room faded (take-apart.mjs)
let updateFillSort;
let sourceScan, indexedScan, originalNavigation, initialNavigation, walkPoses;
let editStore, activeEdits = [], removedIds = new Set(), editsOffline = false, editBusy = false;
let roomPlacements = new Map(), liveRoomAssets = new Map(), movePreview = null;
let foldedRoomParts = new Map();
const tracedFragments = new Map();
const instancesById = new Map();
const instanceSizes = new Map();

async function init() {
  const [twin, poses] = await Promise.all([
    fetch("./data/digital_twin.json").then(res => res.json()),
    fetch("./data/walk_poses.json").then(res => res.ok ? res.json() : null).catch(() => null),
  ]);
  state.twin = twin;
  state.roomAssets = twin.assets;
  originalNavigation = twin.navigation;
  walkPoses = Array.isArray(poses) ? poses : [];
  if (walkPoses.length) twin.navigation = withWalkthroughPassages(twin.navigation, twin.assets, walkPoses);
  initialNavigation = twin.navigation;
  equipmentObstacles = manualObstacles(state.twin.assets, state.twin.navigation.grid_meta.robot_radius_m);

  for (const a of state.twin.assets) {
    state.assetsById.set(a.asset_id, a);
    state.assetsByObj.set(a.id, a);
  }
  state.twin.assets.forEach((asset, i) => instancesById.set(asset.id, i + 1));

  const first = state.twin.navigation.primary_route.waypoints[0];
  state.journey = createJourney({asset_id:null,tag:"Explore the room",kind:"idle",duration_s:0,total_distance_m:0,waypoints:[first],validated:true});
  const targetAsset = state.assetsById.get(state.twin.target_asset_id) || state.twin.assets[0];
  state.selectedAsset = targetAsset;

  updateSidebarStats();
  initThreeScene();
  await loadEwaGaussianSplats("./data/boiler_room_points.bin");
  buildAssetBoundingBoxes();
  mountExplodeControls();
  takeApart = createTakeApart({
    camera, controls, state, uniforms: () => splatMaterial?.uniforms, setPreset: applyCameraPreset,
    container: document.getElementById("canvas-container"), scene, renderer,
  });
  buildRobotAvatar();
  editStore = roomEditStore(id => canRemove(state.assetsByObj.get(id)));
  roomPlacements = loadRoomPlacements(state.assetsByObj, canRemove);
  const savedEdits = await editStore.load();
  applyRoomEdits(savedEdits, true);
  buildRouteMap();
  rebuildRouteGeometry();

  bindUIEvents();
  renderProposalList();
  selectAsset(targetAsset, false);
  applyCameraPreset("overview");
  const apartHash = decodeURIComponent(location.hash).match(/^#apart=(.+)$/);
  if (apartHash && liveRoomAssets.has(apartHash[1])) {
    selectAsset(liveRoomAssets.get(apartHash[1]), false);
    takeApart.open(state.selectedAsset);
  }

  let lastTs = performance.now();
  function animate(now) {
    requestAnimationFrame(animate);
    const dt = Math.min(0.08, (now - lastTs) / 1000);
    lastTs = now;
    updateAttractMode(now);
    stepSimulation(dt, now * 0.001);
    takeApart.frame(boxMeshes);
    if (bimView.active) {
      bimView.update(currentNavigation,state.journey,state.selectedAsset);
      renderer.render(bimView.scene,camera);
    } else {
      maybeTriggerDepthSort();
      renderer.render(scene, camera);
      updateHudLabels();
    }
  }
  requestAnimationFrame(animate);
}

function updateSidebarStats() {
  const assets = state.roomAssets;
  const mapped = assets.filter((a) => a.mapped).length;
  const alerts = assets.filter((a) => a.mapped && assetPresentation(a).needsAttention).length;
  document.getElementById("sidebar-stats-text").textContent =
    `${mapped} registered assets · ${alerts} need attention`;
}

function initThreeScene() {
  const container = document.getElementById("canvas-container");
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x16191b);

  camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.08, 45);

  renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  const gl = renderer.getContext();
  const viewportLimits = gl.getParameter(gl.MAX_VIEWPORT_DIMS);
  maxRenderDimension = Math.min(gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),
    renderer.capabilities.maxTextureSize, viewportLimits[0], viewportLimits[1]);
  renderer.setClearColor(0x16191b, 1);
  renderer.domElement.setAttribute("aria-label", "Equipment Room 3D Gaussian scene and inspection robot");
  container.insertBefore(renderer.domElement, container.firstChild);
  resizeRenderer();

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.09;
  controls.maxDistance = 12;
  controls.minDistance = 0.25;
  controls.minPolarAngle = 0.15;
  controls.maxPolarAngle = Math.PI * 0.48;

  scene.add(new THREE.AmbientLight(0xffffff, 1.2));
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
  dirLight.position.set(1.5, 4.5, -1.5);
  scene.add(dirLight);

  new ResizeObserver(resizeRenderer).observe(container);

  // Raycast click on visible 3D bounding boxes
  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2();
  let downPos = { x: 0, y: 0 };
  renderer.domElement.addEventListener("pointerdown", (e) => {
    downPos = { x: e.clientX, y: e.clientY };
  });
  renderer.domElement.addEventListener("pointerup", (e) => {
    if (Math.hypot(e.clientX - downPos.x, e.clientY - downPos.y) > 5) return;
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);
    if (movePreview) {
      const point = raycaster.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0,1,0),0),new THREE.Vector3());
      if (point) setMovePoint(point);
      return;
    }
    if (bimView?.active) {
      const asset=bimView.pick(raycaster);
      if (asset) selectAsset(asset,true);
      return;
    }
    const pickables = [];
    for (const entry of boxMeshes.values()) {
      if (entry.group.visible) pickables.push(entry.fillMesh);
    }
    const hits = raycaster.intersectObjects(pickables, false);
    if (hits.length > 0) {
      const objId = hits[0].object.userData.objectId;
      const asset = state.assetsByObj.get(objId);
      if (asset) selectAsset(asset, true);
    }
  });
}

function resizeRenderer() {
  const container = document.getElementById("canvas-container");
  const width = container.clientWidth, height = container.clientHeight;
  const quality = resolveRenderQuality({
    quality: document.getElementById("select-splat-mode").value,
    cssWidth: width, cssHeight: height,
    devicePixelRatio: window.devicePixelRatio, maxDimension: maxRenderDimension,
  });
  if (!quality) return;
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  renderer.setDrawingBufferSize(width, height, quality.pixelRatio);
  renderer.domElement.style.width = `${width}px`;
  renderer.domElement.style.height = `${height}px`;
  updateSplatViewportUniforms();
}

function updateSplatViewportUniforms() {
  if (!splatMaterial) return;
  const size = renderer.getDrawingBufferSize(splatMaterial.uniforms.uViewport.value);
  const projection = camera.projectionMatrix.elements;
  splatMaterial.uniforms.uFocal.value.set(0.5 * size.x * projection[0], 0.5 * size.y * projection[5]);
  splatMaterial.uniforms.uSplatAxisLimit.value = 90 * renderer.getPixelRatio();
}

async function loadEwaGaussianSplats(url) {
  const res = await fetch(url);
  const buf = await res.arrayBuffer();
  const header = new DataView(buf, 0, 16);
  const count = header.getUint32(8, true);
  const stride = header.getUint32(12, true) || 40;

  const TEX_W = 2048;
  const TEX_H = Math.ceil(count / TEX_W);
  const totalTexels = TEX_W * TEX_H;

  const centerColorData = new Float32Array(totalTexels * 4);
  const covAData = new Float32Array(totalTexels * 4);
  const covBData = new Float32Array(totalTexels * 4);
  const workerPositions = new Float32Array(count * 3);

  const dv = new DataView(buf, 16);
  const u8 = new Uint8Array(buf, 16);
  for (let i = 0; i < count; i++) {
    const off = i * stride;
    const x = dv.getFloat32(off + 0, true);
    const y = dv.getFloat32(off + 4, true);
    const z = dv.getFloat32(off + 8, true);
    const rByte = u8[off + 12];
    const gByte = u8[off + 13];
    const bByte = u8[off + 14];
    const aByte = u8[off + 15];

    workerPositions[i * 3 + 0] = x;
    workerPositions[i * 3 + 1] = y;
    workerPositions[i * 3 + 2] = z;

    const t4 = i * 4;
    centerColorData[t4 + 0] = x;
    centerColorData[t4 + 1] = y;
    centerColorData[t4 + 2] = z;
    // Pack 16-bit (r + g*256) losslessly in float32 (exact up to 2^24)
    centerColorData[t4 + 3] = rByte + gByte * 256.0;

    if (stride >= 40) {
      covAData[t4 + 0] = dv.getFloat32(off + 16, true); // c_xx
      covAData[t4 + 1] = dv.getFloat32(off + 20, true); // c_xy
      covAData[t4 + 2] = dv.getFloat32(off + 24, true); // c_xz
      covAData[t4 + 3] = dv.getFloat32(off + 28, true); // c_yy
      covBData[t4 + 0] = dv.getFloat32(off + 32, true); // c_yz
      covBData[t4 + 1] = dv.getFloat32(off + 36, true); // c_zz
    } else {
      covAData[t4 + 0] = 0.0002;
      covAData[t4 + 3] = 0.0002;
      covBData[t4 + 1] = 0.0002;
    }
    covBData[t4 + 2] = bByte / 255.0;
    covBData[t4 + 3] = aByte / 255.0;
  }

  const makeFloatTex = (arr) => {
    const t = new THREE.DataTexture(arr, TEX_W, TEX_H, THREE.RGBAFormat, THREE.FloatType);
    t.magFilter = THREE.NearestFilter;
    t.minFilter = THREE.NearestFilter;
    t.generateMipmaps = false;
    t.needsUpdate = true;
    return t;
  };

  const centerTex = makeFloatTex(centerColorData);
  const covATex = makeFloatTex(covAData);
  const covBTex = makeFloatTex(covBData);

  const baseQuad = new THREE.PlaneGeometry(6, 6);
  const instGeom = new THREE.InstancedBufferGeometry();
  instGeom.index = baseQuad.index;
  instGeom.attributes.position = baseQuad.attributes.position;

  const initialOrder = new Float32Array(count + 16384);
  for (let i = 0; i < count; i++) initialOrder[i] = i;
  const splatIdxAttr = new THREE.InstancedBufferAttribute(initialOrder, 1);
  splatIdxAttr.setUsage(THREE.DynamicDrawUsage);
  instGeom.setAttribute("splatIndex", splatIdxAttr);
  instGeom.instanceCount = count;

  document.querySelector("#select-splat-mode option[value=high]").textContent = "High detail";
  document.getElementById("btn-all-boxes").textContent = "All";
  const explodeUniforms = await loadInstanceUniforms("./data/splat_instance.bin", count, TEX_W, TEX_H, state.twin.assets);
  splatMaterial = new THREE.ShaderMaterial({
    precision: "highp",
    uniforms: {
      uCenterTex: { value: centerTex },
      uCovATex: { value: covATex },
      uCovBTex: { value: covBTex },
      uTexSize: { value: new THREE.Vector2(TEX_W, TEX_H) },
      uViewport: { value: new THREE.Vector2(1280, 720) },
      uFocal: { value: new THREE.Vector2(900, 900) },
      ...explodeUniforms,
      uBaseCount: {value:count},
      uFillCount: {value:0},
      uRoomFill: {value:makeRoomTexture(new Float32Array(12),3,1)},
      uRoomFillSize: {value:new THREE.Vector2(3,1)},
      uRemoved: {value:makeRoomTexture(new Float32Array(256 * explodeUniforms.uInstTableSize.value.y),256,
        explodeUniforms.uInstTableSize.value.y,THREE.RedFormat)},
      uHasRemoved: {value:0},
      uFillDebug: {value:0},
      uSplatAxisLimit: { value: 180 },
    },
    vertexShader: `
      attribute float splatIndex;
      varying vec4 vColor;
      varying vec2 vQuadCoord;

      uniform sampler2D uCenterTex;
      uniform sampler2D uCovATex;
      uniform sampler2D uCovBTex;
      uniform vec2 uTexSize;
      uniform vec2 uViewport;
      uniform vec2 uFocal;
${SHADER_DECL}      uniform float uSplatAxisLimit;
      uniform float uBaseCount, uFillCount, uHasRemoved, uFillDebug;
      uniform sampler2D uRoomFill, uRemoved;
      uniform vec2 uRoomFillSize;

      void main() {
        float col = mod(splatIndex, uTexSize.x);
        float row = floor(splatIndex / uTexSize.x);
        vec2 uv = (vec2(col, row) + 0.5) / uTexSize;

        bool synthetic = splatIndex >= uBaseCount;
        float fillIndex = splatIndex - uBaseCount;
        if (synthetic && fillIndex >= uFillCount) {
          gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
          return;
        }
        float fillWidth = uRoomFillSize.x / 3.0;
        vec2 fillUv = (vec2(3.0 * mod(fillIndex, fillWidth), floor(fillIndex / fillWidth)) + 0.5) / uRoomFillSize;
        vec4 fillCovB = synthetic ? texture2D(uRoomFill, fillUv + vec2(2.0 / uRoomFillSize.x, 0.0)) : vec4(0.0);
        vec4 centerRgba = synthetic ? texture2D(uRoomFill, fillUv) : texture2D(uCenterTex, uv);
        vec3 center = centerRgba.xyz;
${SHADER_OFFSET}        vec4 cam = modelViewMatrix * vec4(center, 1.0);

        if (cam.z > -0.08) {
          gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
          return;
        }

        vec4 pos2d = projectionMatrix * cam;
        float clip = 1.35 * pos2d.w;
        if (pos2d.x < -clip || pos2d.x > clip || pos2d.y < -clip || pos2d.y > clip) {
          gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
          return;
        }

        vec4 covA = synthetic ? texture2D(uRoomFill, fillUv + vec2(1.0 / uRoomFillSize.x, 0.0)) : texture2D(uCovATex, uv);
        vec4 covB = synthetic ? fillCovB : texture2D(uCovBTex, uv);

        // Unpack lossless RGBA
        float rg = centerRgba.w;
        float r = mod(rg, 256.0) / 255.0;
        float g = floor(rg / 256.0) / 255.0;
        float b = covB.z;
        float a = covB.w < 0.0 ? fract(-covB.w) * 2.0 : covB.w;

        mat3 Vrk = mat3(
          covA.x, covA.y, covA.z,
          covA.y, covA.w, covB.x,
          covA.z, covB.x, covB.y
        );

        float tz = -cam.z;
        float tz2 = tz * tz;
        // Clamp fov ratio at peripheral angles for EWA stability (Zwicker et al. / 3DGS)
        float limX = 1.3 * tz;
        float limY = 1.3 * tz;
        float cxClamped = clamp(cam.x, -limX, limX);
        float cyClamped = clamp(cam.y, -limY, limY);

        vec3 ju = vec3(uFocal.x / tz, 0.0, (uFocal.x * cxClamped) / tz2);
        vec3 jv = vec3(0.0, uFocal.y / tz, (uFocal.y * cyClamped) / tz2);

        mat3 Rview = mat3(modelViewMatrix);
        vec3 wu = ju * Rview;
        vec3 wv = jv * Rview;

        vec3 v_wu = Vrk * wu;
        vec3 v_wv = Vrk * wv;

        float c_a = dot(wu, v_wu) + 0.15;
        float c_b = dot(wu, v_wv);
        float c_c = dot(wv, v_wv) + 0.15;

        float mid = 0.5 * (c_a + c_c);
        float disc = max(0.0001, (c_a - c_c) * (c_a - c_c) * 0.25 + c_b * c_b);
        float radius = sqrt(disc);
        float lambda1 = mid + radius;
        float lambda2 = max(0.15, mid - radius);

        // Numerically stable 2D eigenvector for both c_a >= c_c and c_a < c_c
        vec2 diagVec = (c_a >= c_c)
          ? normalize(vec2(max(lambda1 - c_c, 1e-6), c_b))
          : normalize(vec2(c_b, max(lambda1 - c_a, 1e-6)));

        vec2 majorAxis = min(2.0 * sqrt(lambda1), uSplatAxisLimit) * diagVec;
        vec2 minorAxis = min(2.0 * sqrt(lambda2), uSplatAxisLimit) * vec2(-diagVec.y, diagVec.x);

        vColor = vec4(r, g, b, a);
${SHADER_COLOR}        if (synthetic && instId < 0.5 && uFillDebug > 0.5) vColor.rgb = mix(vColor.rgb, vec3(1.0, 0.2, 0.65), 0.82);
        vQuadCoord = position.xy;

        vec2 offsetClip = (position.x * majorAxis + position.y * minorAxis) / uViewport;
        gl_Position = vec4(pos2d.xy + offsetClip * pos2d.w, pos2d.z, pos2d.w);
      }
    `,
    fragmentShader: `
      varying vec4 vColor;
      varying vec2 vQuadCoord;
      void main() {
        float power = -0.5 * dot(vQuadCoord, vQuadCoord);
        if (power < -4.5) discard;
        float alpha = min(0.99, vColor.a * exp(power));
        if (alpha < 0.0039216) discard;
        gl_FragColor = vec4(vColor.rgb * alpha, alpha);
      }
    `,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: THREE.CustomBlending,
    blendSrc: THREE.OneFactor,
    blendDst: THREE.OneMinusSrcAlphaFactor,
    side: THREE.DoubleSide,
  });

  updateSplatViewportUniforms();
  splatMesh = new THREE.Mesh(instGeom, splatMaterial);
  splatMesh.frustumCulled = false;
  splatMesh.renderOrder = 1;
  scene.add(splatMesh);

  const instanceTexture = explodeUniforms.uInstTex.value;
  if (instanceTexture.userData.complete) {
    const instances = instanceTexture.image.data;
    const counts = new Uint32Array(state.twin.assets.length + 1);
    for (let i=0;i<count;i++) counts[instances[i]]++;
    for (const [id,index] of instancesById) instanceSizes.set(id,counts[index]);
    sourceScan = [centerColorData,covAData,covBData,instances,count];
    fitBimVessels(state.twin.assets,centerColorData,covBData,instances,count);
  }
  initSortWorker(workerPositions, instGeom);
}

function makeRoomTexture(data, width, height, format=THREE.RGBAFormat) {
  const texture = new THREE.DataTexture(data,width,height,format,THREE.FloatType);
  texture.needsUpdate = true;
  return texture;
}

function initSortWorker(positions, geometry) {
  const workerCode = `
    let pos = null;
    let fills = new Float32Array(0);
    let revision = 0;
    let count = 0;
    let depthInt = null;
    let counts = new Uint32Array(65536);

    self.onmessage = function(e) {
      if (e.data.positions) {
        pos = e.data.positions;
        count = pos.length / 3;
        depthInt = new Uint16Array(count);
        return;
      }
      if (e.data.fills) {
        fills = e.data.fills;
        revision = e.data.revision;
        count = (pos.length + fills.length) / 3;
        depthInt = new Uint16Array(count);
        return;
      }
      if (!pos) return;
      const vx = e.data.viewZ[0];
      const vy = e.data.viewZ[1];
      const vz = e.data.viewZ[2];
      const vw = e.data.viewZ[3];

      const depth = i => {
        const k = i*3;
        const a = k < pos.length ? pos : fills;
        const j = k < pos.length ? k : k - pos.length;
        return a[j]*vx + a[j+1]*vy + a[j+2]*vz + vw;
      };
      let minD = 1e9, maxD = -1e9;
      // Compute camera Z (in Three.js camera looks down -Z, so more negative Z is further away; sort back-to-front = ascending Z)
      for (let i = 0; i < count; i++) {
        const z = depth(i);
        if (z < minD) minD = z;
        if (z > maxD) maxD = z;
      }
      const scale = 65535.0 / Math.max(1e-4, maxD - minD);
      counts.fill(0);
      for (let i = 0; i < count; i++) {
        const z = depth(i);
        const b = Math.min(65535, Math.max(0, ((z - minD) * scale) | 0));
        depthInt[i] = b;
        counts[b]++;
      }
      let sum = 0;
      for (let b = 0; b < 65536; b++) {
        const c = counts[b];
        counts[b] = sum;
        sum += c;
      }
      const out = new Float32Array(count);
      for (let i = 0; i < count; i++) {
        const b = depthInt[i];
        out[counts[b]++] = i;
      }
      self.postMessage({ sorted: out, revision }, [out.buffer]);
    };
  `;
  const blob = new Blob([workerCode], { type: "application/javascript" });
  sortWorker = new Worker(URL.createObjectURL(blob));
  sortWorker.postMessage({ positions }, [positions.buffer]);

  let revision = 0;
  updateFillSort = fills => {
    sortWorker.postMessage({fills,revision:++revision},[fills.buffer]);
    lastSortCamKey = "";
  };
  sortWorker.onmessage = (e) => {
    sortBusy = false;
    if (e.data.revision !== revision) return;
    const sorted = e.data.sorted;
    let attribute = geometry.getAttribute("splatIndex");
    if (attribute.array.length < sorted.length) {
      geometry.dispose();
      attribute = new THREE.InstancedBufferAttribute(new Float32Array(sorted.length + 16384),1);
      attribute.setUsage(THREE.DynamicDrawUsage);
      geometry.setAttribute("splatIndex",attribute);
    }
    attribute.array.set(sorted);
    attribute.needsUpdate = true;
    geometry.instanceCount = sorted.length;
  };
}

function maybeTriggerDepthSort() {
  if (!sortWorker || sortBusy) return;
  camera.updateMatrixWorld();
  const m = camera.matrixWorldInverse.elements;
  // Row 2 of matrixWorldInverse is [m[2], m[6], m[10], m[14]]
  const key = `${m[2].toFixed(3)},${m[6].toFixed(3)},${m[10].toFixed(3)},${m[14].toFixed(3)}`;
  if (key === lastSortCamKey) return;
  lastSortCamKey = key;
  sortBusy = true;
  sortWorker.postMessage({ viewZ: [m[2], m[6], m[10], m[14]] });
}

function applyCameraPreset(preset) {
  if (preset === "bim" && takeApart?.isOpen) takeApart.close();
  state.cameraPreset = preset;
  controls.enabled = preset !== "first" && !(state.manualWalk && preset === "walk");
  document.querySelectorAll("[data-cam]").forEach((b) => {
    b.classList.toggle("active", b.dataset.cam === preset);
  });
  if (preset === "bim") {
    setInspectorOpen(false);
    bimView.setRoom(state.roomAssets,state.twin.scene.walls,state.twin.navigation.grid_meta);
  }
  bimView?.setActive(preset === "bim");

  if (preset === "overview") {
    camera.position.set(-0.35, 1.48, 1.85);
    controls.target.set(0.25, 0.88, -1.75);
    controls.update();
  } else if (preset === "inside" || preset === "detail") {
    const target = state.assetsById.get(state.twin.target_asset_id);
    const asset = preset === "detail" ? state.selectedAsset || target : target;
    if (!asset) return;
    const pose = inspectionCameraPose(state.twin.navigation, asset, robotPosition());
    camera.position.fromArray(pose.position);
    controls.target.fromArray(pose.target);
    controls.update();
  }
}

function getAssetBoxColor(asset) {
  if (asset.movable) return 0x8b5cf6; // movable object (changeable obstacle)
  const presentation = assetPresentation(asset);
  if (presentation.frameColor !== null) return presentation.frameColor;
  if (asset.is_target || asset.condition_state === "degraded" || asset.condition_state === "attention_required") {
    return 0xd94838; // Field Atlas Terracotta Red
  }
  if (asset.condition_state === "watch") {
    return 0xc97a16; // Warm Amber
  }
  return 0x2b6cb0;   // Clean Engineering Blue
}

// "Every object, its own asset": stable distinct hue per extracted object
function instanceColor(id) {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return new THREE.Color().setHSL(((h * 0.618033988749895) % 1), 0.75, 0.6).getHex();
}

function buildAssetBoundingBoxes() {
  const hudContainer = document.getElementById("hud-overlay");
  hudContainer.innerHTML = "";

  for (const asset of state.twin.assets) {
    const [sx, sy, sz] = asset.size;
    const [cx, cy, cz] = asset.center;
    const hex = getAssetBoxColor(asset);

    const group = new THREE.Group();
    group.position.set(cx, cy, cz);
    group.rotation.y = -asset.yaw;
    group.renderOrder = 5;

    let boxGeom;
    const cylinder = asset.cylinder;
    if (cylinder?.start?.length === 3 && cylinder.end?.length === 3 && cylinder.radius > 0
        && [cylinder.radius, ...cylinder.start, ...cylinder.end].every(Number.isFinite)) {
      const inverseYaw = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), asset.yaw);
      const start = new THREE.Vector3().fromArray(cylinder.start).sub(group.position).applyQuaternion(inverseYaw);
      const end = new THREE.Vector3().fromArray(cylinder.end).sub(group.position).applyQuaternion(inverseYaw);
      const axis = end.clone().sub(start);
      const length = axis.length();
      if (length > 0) {
        boxGeom = new THREE.CylinderGeometry(cylinder.radius, cylinder.radius, length, 12, 1, true);
        boxGeom.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), axis.normalize()));
        boxGeom.translate(...start.add(end).multiplyScalar(0.5).toArray());
      }
    }
    boxGeom ??= new THREE.BoxGeometry(sx, sy, sz);
    const edges = new THREE.EdgesGeometry(boxGeom);
    const lineMat = new THREE.LineBasicMaterial({
      color: hex,
      transparent: true,
      opacity: 0.95,
      depthTest: false, // frames stay visible inside dense splats
    });
    const wire = new THREE.LineSegments(edges, lineMat);
    group.add(wire);

    const fillMat = new THREE.MeshBasicMaterial({
      color: hex,
      transparent: true,
      opacity: 0.04,
      depthWrite: false,
      depthTest: false,
    });
    const fillMesh = new THREE.Mesh(boxGeom, fillMat);
    fillMesh.userData.objectId = asset.id;
    group.add(fillMesh);

    scene.add(group);
    boxMeshes.set(asset.id, { group, wire, lineMat, fillMesh, fillMat, asset });

    {
      const tagEl = document.createElement("div");
      tagEl.className = `hud-tag ${asset.condition_state} ${assetPresentation(asset).kind}`;
      tagEl.textContent = `${asset.tag} · ${asset.label}`;
      tagEl.setAttribute("role", "button");
      tagEl.tabIndex = 0;
      tagEl.addEventListener("keydown", e => { if (e.key === "Enter") selectAsset(asset, true); });
      tagEl.addEventListener("click", (e) => {
        e.stopPropagation();
        selectAsset(asset, true);
      });
      hudContainer.appendChild(tagEl);
      hudElements.set(asset.id, tagEl);
    }
  }
}

function buildRobotAvatar() {
  robotGroup = new THREE.Group();
  robotGroup.renderOrder = 6;
  const radius = state.twin.navigation.grid_meta.robot_radius_m;
  const widthScale = radius / .15;

  const baseMesh = new THREE.Mesh(
    new THREE.CylinderGeometry(0.13 * widthScale, radius, 0.10, 24),
    new THREE.MeshStandardMaterial({ color: 0x172622, metalness: 0.4, roughness: 0.4 })
  );
  baseMesh.position.y = 0.06;
  robotGroup.add(baseMesh);

  const mast = new THREE.Mesh(
    new THREE.CylinderGeometry(0.025, 0.035, 0.44, 16),
    new THREE.MeshStandardMaterial({ color: 0x475569, metalness: 0.4, roughness: 0.4 })
  );
  mast.position.y = 0.32;
  robotGroup.add(mast);

  const head = new THREE.Mesh(
    new THREE.BoxGeometry(0.14 * widthScale, 0.09, 0.18 * widthScale),
    new THREE.MeshStandardMaterial({ color: 0x1e293b, metalness: 0.5, roughness: 0.3 })
  );
  head.position.y = 0.56;
  robotGroup.add(head);

  lidarRing = new THREE.Mesh(
    new THREE.CylinderGeometry(0.05, 0.05, 0.025, 16, 1, true),
    new THREE.MeshBasicMaterial({ color: 0x8fd14f, wireframe: true })
  );
  lidarRing.position.y = 0.63;
  robotGroup.add(lidarRing);

  const proxRadius = state.twin.navigation.grid_meta.proximity_radius_m || 1.65;
  proxRing = new THREE.Mesh(
    new THREE.RingGeometry(proxRadius - 0.02, proxRadius, 64),
    new THREE.MeshBasicMaterial({ color: 0x8fd14f, side: THREE.DoubleSide, transparent: true, opacity: 0.42 })
  );
  proxRing.rotation.x = -Math.PI / 2;
  proxRing.position.y = 0.02;
  proxRing.visible = false;
  robotGroup.add(proxRing);

  scene.add(robotGroup);
  robotGroup.position.fromArray(state.journey.route.waypoints[0].position);
}

function buildRouteMap() { rebuildReachableOverlay(); }

function rebuildReachableOverlay() {
  const navigation=state.twin.navigation,m=navigation.grid_meta;
  const position=robotPosition();
  const cell=cellAtPosition(position);
  const [x,z]=navigation.start_xz;
  const floor=reachableFloor(navigation,cell ? position : {x,z});
  mapBackground=createGameMap(navigation,floor,state.roomAssets);
  const legend = document.getElementById("map-coverage");
  legend.textContent = navigation.walkthrough_passages ? "Teal · Walkthrough passages" : "Outlined objects block movement";
  legend.classList.toggle("walkthrough-legend", !!navigation.walkthrough_passages);
}

function drawRouteMap(nav) {
  const canvas=document.getElementById("route-map"),ctx=canvas.getContext("2d");
  const m=state.twin.navigation.grid_meta;
  const frame=mapFrame(canvas.width,canvas.height);
  const project=(x,z)=>mapPoint(m,canvas.width,canvas.height,x,z);
  ctx.drawImage(mapBackground,0,0,canvas.width,canvas.height);
  ctx.save();ctx.beginPath();ctx.rect(frame.x+6,frame.y+6,frame.width-12,frame.height-12);ctx.clip();
  const route=state.journey.route,active=route.kind!=="idle";
  ctx.lineJoin="round";ctx.lineCap="round";
  if (state.previousRoute) {
    ctx.strokeStyle="#c0c9cc66";ctx.lineWidth=3;ctx.setLineDash([7,7]);ctx.beginPath();
    state.previousRoute.waypoints.forEach((w,i)=>{const p=project(w.position[0],w.position[2]);if(i)ctx.lineTo(...p);else ctx.moveTo(...p);});ctx.stroke();ctx.setLineDash([]);
  }
  if (state.demoObstacle) {
    const o=state.demoObstacle,a=project(o.x-o.size/2,o.z+o.size/2),b=project(o.x+o.size/2,o.z-o.size/2);
    ctx.fillStyle="#d99044";ctx.strokeStyle="#ffdc9e";ctx.lineWidth=2;
    ctx.fillRect(a[0],a[1],b[0]-a[0],b[1]-a[1]);ctx.strokeRect(a[0],a[1],b[0]-a[0],b[1]-a[1]);
  }
  if (movePreview?.placement) {
    const [x,,z]=movePreview.placement.center;
    ctx.beginPath();ctx.arc(...project(x,z),9,0,Math.PI*2);
    ctx.strokeStyle=movePreview.error ? '#ff9b85' : '#79e2ef';ctx.lineWidth=3;ctx.stroke();
  }
  if(active) {
    const points=route.waypoints;
    ctx.lineWidth=8;ctx.strokeStyle="#0b1722";ctx.beginPath();
    points.forEach((w,i)=>{const p=project(w.position[0],w.position[2]);if(i)ctx.lineTo(...p);else ctx.moveTo(...p);});ctx.stroke();
    ctx.lineWidth=3;ctx.strokeStyle="#8c8970";ctx.stroke();
    ctx.lineWidth=5;ctx.strokeStyle="#f4cf76";ctx.shadowColor="#f4cf7650";ctx.shadowBlur=8;
    ctx.beginPath();ctx.moveTo(...project(nav.x,nav.z));
    for(const w of points)if(w.distance_m>nav.distance)ctx.lineTo(...project(w.position[0],w.position[2]));
    ctx.stroke();ctx.shadowBlur=0;
    for(const [index,stop] of (route.stops || []).entries()) {
      const p=project(stop.x,stop.z);
      ctx.fillStyle="#1c3039";ctx.strokeStyle="#e7cd8e";ctx.lineWidth=2;
      ctx.beginPath();ctx.arc(...p,10,0,Math.PI*2);ctx.fill();ctx.stroke();
      ctx.fillStyle="#f4dfb0";ctx.textAlign="center";ctx.font="bold 14px sans-serif";ctx.fillText(String(index+1),p[0],p[1]+5);
    }
    const end=points.at(-1).position,goal=project(end[0],end[2]);
    ctx.save();ctx.translate(...goal);ctx.rotate(Math.PI/4);ctx.fillStyle="#f4cf76";ctx.strokeStyle="#0e1b24";ctx.lineWidth=3;
    ctx.fillRect(-7,-7,14,14);ctx.strokeRect(-7,-7,14,14);ctx.restore();
    const target=route.asset_id ? roomAsset(state.assetsById.get(route.asset_id)) : null;
    if(target) {
      const p=project(target.center[0],target.center[2]);
      ctx.strokeStyle="#ff8a7866";ctx.lineWidth=2;ctx.setLineDash([5,6]);ctx.beginPath();ctx.moveTo(...goal);ctx.lineTo(...p);ctx.stroke();ctx.setLineDash([]);
      ctx.fillStyle="#ff8978";ctx.strokeStyle="#172129";ctx.lineWidth=3;ctx.beginPath();ctx.arc(...p,8,0,Math.PI*2);ctx.fill();ctx.stroke();
      ctx.font="bold 21px sans-serif";ctx.textAlign="right";ctx.strokeStyle="#142129";ctx.lineWidth=5;
      const x=Math.max(frame.x+95,Math.min(frame.x+frame.width-12,p[0]-12)),y=Math.max(frame.y+22,p[1]-12);
      ctx.strokeText(target.tag,x,y);ctx.fillText(target.tag,x,y);
    }
  }
  const robot=project(nav.x,nav.z);
  ctx.strokeStyle="#72e0ed55";ctx.lineWidth=2;ctx.beginPath();ctx.arc(...robot,20,0,Math.PI*2);ctx.stroke();
  ctx.save();ctx.translate(...robot);ctx.rotate(nav.yaw);
  ctx.fillStyle="#79e2ef";ctx.strokeStyle="#10242e";ctx.lineWidth=3;
  ctx.beginPath();ctx.moveTo(0,-16);ctx.lineTo(-11,11);ctx.lineTo(0,7);ctx.lineTo(11,11);ctx.closePath();ctx.fill();ctx.stroke();ctx.restore();
  ctx.restore();
  // A true one-metre scale, independent of map panel size.
  const length=frame.width/(m.nx*m.res),x=frame.x+frame.width-length,y=canvas.height-12;
  ctx.strokeStyle="#b0c8ce";ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(x,y-5);ctx.lineTo(x,y);ctx.lineTo(x+length,y);ctx.lineTo(x+length,y-5);ctx.stroke();
  ctx.font="15px sans-serif";ctx.fillStyle="#b0c8ce";ctx.textAlign="center";ctx.fillText("1 m",x+length/2,y-9);
}

function rebuildRouteGeometry() {
  if (pathLineGroup) {
    pathLineGroup.traverse(obj => { obj.geometry?.dispose(); if (obj.material) obj.material.dispose(); });
    scene.remove(pathLineGroup);
  }
  pathLineGroup = new THREE.Group();
  pathLineGroup.renderOrder = 4;

  const wps = state.journey.route?.waypoints || [];
  if (wps.length < 2) return;

  const curvePts = wps.map((w) => new THREE.Vector3(w.position[0], 0.028, w.position[2]));
  // Piecewise lines preserve the collision-checked planner trajectory exactly.
  const tubeMat = new THREE.MeshBasicMaterial({ color: 0x8fd14f, transparent: true, opacity: 0.88 });
  for (let i = 1; i < curvePts.length; i++) {
    const delta = new THREE.Vector3().subVectors(curvePts[i], curvePts[i - 1]);
    if (delta.length() < 0.0001) continue;
    const line = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, delta.length(), 6), tubeMat);
    line.position.copy(curvePts[i]).add(curvePts[i - 1]).multiplyScalar(0.5);
    line.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), delta.normalize());
    pathLineGroup.add(line);
  }

  const endMarker = new THREE.Mesh(
    new THREE.RingGeometry(0.10, 0.16, 32),
    new THREE.MeshBasicMaterial({ color: 0xd94838, side: THREE.DoubleSide })
  );
  endMarker.rotation.x = -Math.PI / 2;
  endMarker.position.copy(curvePts[curvePts.length - 1]);
  pathLineGroup.add(endMarker);

  scene.add(pathLineGroup);
}

function stepSimulation(dt, elapsedSec) {
  const route = state.journey.route;
  if (!route || !route.waypoints || route.waypoints.length === 0) return;

  if (state.journey.phase === "moving" && !editBusy && !movePreview) {
    const nextTime=Math.min(route.duration_s,state.journey.elapsed+dt*state.speed);
    try {
      advanceAlongRoute(state.twin.navigation,route,robotPosition(),state.journey.elapsed,nextTime);
      applyJourneyEvent({type:"progress",elapsed:nextTime});
    } catch(error) {
      navigationError(error);
    }
  }

  const sampled = sampleRoute(route, state.journey.elapsed);
  const nav = state.manualWalk ? keyboardWalk(editBusy || movePreview ? 0 : dt) :
    state.journey.phase === "blocked" ? {...sampled,...robotPosition()} : sampled;
  currentNavigation = nav;
  const {x: rx, z: rz, yaw} = nav;
  robotGroup.position.set(rx, 0, rz);
  recordPassedAssets({x:rx,z:rz});
  robotGroup.rotation.y = yaw;
  if (lidarRing) lidarRing.rotation.y = elapsedSec * 5.0;

  robotGroup.visible = state.cameraPreset !== "first";
  if (state.cameraPreset === "first" || (state.cameraPreset === "walk" && state.manualWalk)) {
    updateWalkCamera(nav);
  } else if (state.cameraPreset === "walk") {
    // Both camera and robot follow the validated corridor; no offset through walls.
    const behind = sampleRoute(route, Math.max(0, state.journey.elapsed - 1.2));
    const ahead = sampleRoute(route, Math.min(route.duration_s, state.journey.elapsed + 0.8));
    camera.position.set(behind.x, 1.12, behind.z);
    controls.target.set(ahead.x, 1.05, ahead.z);
    if (state.journey.phase === "arrived" || Math.hypot(ahead.x - behind.x, ahead.z - behind.z) < 0.05) {
      const target = route.asset_id ? roomAsset(state.assetsById.get(route.asset_id)) : null;
      if (target) controls.target.fromArray(target.center);
    }
    controls.update();
  } else controls.update();

  const refreshHud = elapsedSec - hudLastUpdate >= 0.15 || hudLastUpdate < 0;
  if (refreshHud) {
    hudLastUpdate = elapsedSec;
    state.nearby = nearbyAssets(state.roomAssets, rx, rz, state.twin.navigation.grid_meta.proximity_radius_m);
    renderProximityStream(state.nearby);
  }
  const nearbyMap = new Map((state.nearby || []).map(a => [a.id, a.distance_m]));

  // Update 3D box visibility cleanly (matching IMG_4398.HEIC: focus on selected + nearby shortlist)
  for (const [objId, entry] of boxMeshes.entries()) {
    if (entry.removed) continue;
    const a = entry.asset;
    const isNear = nearbyMap.has(objId);
    const isSel = state.selectedAsset && state.selectedAsset.id === objId;

    let vis = false;
    if (state.boxesMode === "off") {
      vis = false;
    } else if (state.boxesMode === "all") {
      vis = true;
    } else {
      // "proximity" mode: show selected, target, and up to nearby canonical Valmet assets
      vis = isSel || a.is_target || isNear;
    }

    entry.group.visible = vis;
    if (!vis) continue;

    if (isSel) {
      entry.lineMat.color.setHex(assetPresentation(a).frameColor ?? 0xd94838);
      entry.lineMat.opacity = 1.0;
      entry.fillMat.opacity = 0.08;
    } else if (a.is_target) {
      entry.lineMat.color.setHex(0xd94838);
      entry.lineMat.opacity = 0.9;
      entry.fillMat.opacity = 0.05;
    } else {
      const col = a.condition_state === "healthy" ? instanceColor(a.id) : getAssetBoxColor(a);
      entry.lineMat.color.setHex(isNear ? col : getAssetBoxColor(a));
      entry.fillMat.color.setHex(isNear ? col : getAssetBoxColor(a));
      entry.lineMat.opacity = isNear ? 0.95 : (a.mapped ? 0.42 : 0.28);
      entry.fillMat.opacity = isNear ? 0.10 : (a.mapped ? 0.02 : 0.005);
    }
    const passiveColor = assetPresentation(a).frameColor;
    if (passiveColor !== null) { entry.lineMat.color.setHex(passiveColor); entry.fillMat.color.setHex(passiveColor); }
  }


  applyExplode(boxMeshes, splatMaterial);
  renderJourneyControls(nav);
  document.getElementById("prox-count").textContent = `${(state.nearby || []).length} in range`;
  drawRouteMap(nav);
  semanticPlan.update(nav,state.journey);

}

function recordPassedAssets(position) {
  const summary = state.walkSummary;
  if (!summary) return;
  const from = summary.previous, dx = position.x - from.x, dz = position.z - from.z;
  const length2 = dx * dx + dz * dz;
  const radius = state.twin.navigation.grid_meta.proximity_radius_m || 1.65;
  for (const asset of state.roomAssets) {
    if (summary.passed.has(asset.id)) continue;
    const t = length2 ? Math.max(0, Math.min(1, ((asset.center[0]-from.x)*dx + (asset.center[2]-from.z)*dz) / length2)) : 0;
    if (Math.hypot(asset.center[0]-from.x-t*dx, asset.center[2]-from.z-t*dz) > radius) continue;
    summary.passed.add(asset.id);
    if (assetPresentation(asset).needsAttention) summary.attention.add(asset.id);
  }
  summary.previous = position;
  const line = document.getElementById("walk-summary");
  const text = `passed ${summary.passed.size} assets · ${summary.attention.size} need attention`;
  if (line.textContent !== text) line.textContent = text;
  line.hidden = false;
}

function updateHudLabels() {
  const container = document.getElementById("canvas-container");
  const w = container.clientWidth;
  const h = container.clientHeight;
  const rx = robotGroup.position.x;
  const rz = robotGroup.position.z;
  const proxR = state.twin.navigation.grid_meta.proximity_radius_m || 1.65;

  const candidates = [];
  for (const [objId, el] of hudElements.entries()) {
    if (state.boxesMode === "off") {
      el.style.display = "none";
      continue;
    }
    const entry = boxMeshes.get(objId);
    if (!entry.group.visible) {
      el.style.display = "none";
      continue;
    }
    const a = entry.asset;
    const isSel = state.selectedAsset && state.selectedAsset.id === objId;
    const dist = Math.hypot(a.center[0] - rx, a.center[2] - rz);
    const isNear = dist <= proxR;

    if (!isSel && !a.is_target && !isNear) {
      el.style.display = "none";
      continue;
    }

    // Anchor label pill to top of the 3D bounding box
    const topPos = new THREE.Vector3(a.center[0], a.center[1] + a.size[1] / 2 + 0.03, a.center[2]).add(explodeOffset(a));
    topPos.project(camera);
    if (topPos.z > 1.0 || topPos.z < -1.0) {
      el.style.display = "none";
      continue;
    }
    const x = (topPos.x * 0.5 + 0.5) * w;
    const y = (-topPos.y * 0.5 + 0.5) * h;
    if (x < 50 || x > w - 50 || y < 50 || y > h - 40) {
      el.style.display = "none";
      continue;
    }

    el.textContent = `${a.tag} · ${dist.toFixed(1)} m · ${a.condition_state.replaceAll("_", " ")}`;
    el.title = `${a.label} · ${a.mapped ? "Excel registry" : `Excel profile from ${a.excel_profile_tag || "registry"}`} · ${a.health_score == null ? "Movable object" : `Health ${a.health_score}%`}`;
    if (assetPresentation(a).notice) el.title = `${a.label} · ${assetPresentation(a).notice}`;
    const prio = isSel ? 0 : a.is_target ? 1 : assetPresentation(a).needsAttention ? 2 : a.mapped ? 3 : 4;
    candidates.push({ el, x, y, dist, prio, isSel });
  }

  candidates.sort((a, b) => a.prio - b.prio || a.dist - b.dist);
  const origin = container.getBoundingClientRect();
  const placed = [];
  for (const panel of document.querySelectorAll(".viewport-Controls-overlay, .display-options-body, .navigation-hud, .route-map-panel, .right-sidebar")) {
    if (!panel.getClientRects().length) continue;
    const r = panel.getBoundingClientRect();
    placed.push({left:r.left-origin.left, right:r.right-origin.left, top:r.top-origin.top, bottom:r.bottom-origin.top});
  }
  let visibleCount = 0;
  for (const c of candidates) {
    c.el.style.display = "none";
    if (visibleCount >= 8) continue;
    c.el.style.display = "block";
    c.el.style.visibility = "hidden";
    const measured = c.el.getBoundingClientRect();
    const width = measured.width, height = measured.height;
    if (width > w - 16 || height > h - 16) { c.el.style.display = "none"; continue; }
    const left = THREE.MathUtils.clamp(c.x - width / 2, 8, w - width - 8);
    let accepted = false;
    for (const offset of [0, -30, 30, -60, 60, -90, 90]) {
      const top = c.y + offset - height;
      const rect = {left, right:left+width, top, bottom:top+height};
      if (rect.top < 8 || rect.bottom > h - 8) continue;
      if (placed.some(p => rect.left < p.right + 6 && rect.right > p.left - 6 && rect.top < p.bottom + 6 && rect.bottom > p.top - 6)) continue;
      placed.push(rect);
      c.el.style.left = `${left}px`;
      c.el.style.top = `${top}px`;
      c.el.style.visibility = "visible";
      visibleCount++;
      accepted = true;
      break;
    }
    if (!accepted) c.el.style.display = "none";
  }

}

function renderProximityStream(mappedNearby) {
  const listEl = document.getElementById("proximity-list");
  const key = mappedNearby.map(n => `${n.id}:${n.distance_m.toFixed(1)}`).join("|");
  if (key === proximityKey) return;
  proximityKey = key;
  listEl.innerHTML = mappedNearby
    .map((n) => {
      const a = state.assetsByObj.get(n.id);
      const cls = n.is_target ? "target" : n.condition_state;
      const kind = assetPresentation(n).kind;
      const status = kind === "instrumented" ? (n.mapped ? `${n.health_score}%` : "procedural") : kind;
      return `
        <div class="prox-row ${cls}" data-obj="${n.id}">
          <span>${n.tag} · ${a ? a.label : ""}</span>
          <span>${n.distance_m.toFixed(1)}m · ${status}</span>
        </div>
      `;
    })
    .join("");

  for (const row of listEl.querySelectorAll(".prox-row")) {
    row.addEventListener("click", () => {
      const a = state.assetsByObj.get(row.dataset.obj);
      if (a) selectAsset(a, true);
    });
  }
}

function renderProposalList() {
  const listEl = document.getElementById("asset-list");
  const q = state.searchQuery.trim().toLowerCase();

  const filtered = state.roomAssets.filter((a) => {
    if (state.visualReviewFilter === "alerts" && !(a.mapped && assetPresentation(a).needsAttention)) return false;
    if (state.visualReviewFilter === "mapped" && !a.mapped) return false;
    if (state.classFilter !== "all" && a.class !== state.classFilter) return false;
    if (!q) return true;
    return (
      a.tag.toLowerCase().includes(q) ||
      a.asset_id.toLowerCase().includes(q) ||
      a.id.toLowerCase().includes(q) ||
      a.label.toLowerCase().includes(q)
    );
  });

  listEl.innerHTML = filtered
    .map((a) => {
      const sel = state.selectedAsset && state.selectedAsset.id === a.id ? "selected" : "";
      const stateCls =
        a.condition_state === "degraded" || a.condition_state === "attention_required"
          ? "prop-badge-degraded"
          : a.condition_state === "watch"
          ? "prop-badge-watch"
          : "";
      return `
        <div class="proposal-card ${sel}" data-obj="${a.id}">
          <div class="prop-class">${a.label}</div>
          <div class="prop-meta">
            <span>${a.tag} · ${a.asset_id}</span>
            <span class="${stateCls}">${assetPresentation(a).notice || (a.health_score == null ? "No health reading" : `Health ${a.health_score}%`)} · ${a.condition_state}</span>
          </div>
        </div>
      `;
    })
    .join("");

  for (const card of listEl.querySelectorAll(".proposal-card")) {
    card.addEventListener("click", () => {
      const a = state.assetsByObj.get(card.dataset.obj);
      if (a) selectAsset(a, true);
    });
  }
}

function drawDetectionCrop(asset) {
  const canvas = document.getElementById("detection-canvas");
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;

  const cv = asset.camera_view;
  const score = document.getElementById("detail-station-score");
  const note = document.getElementById("detail-detector-prompt");
  ctx.clearRect(0, 0, W, H);
  canvas.hidden = true;
  score.textContent = "";
  if (!cv?.image || !Array.isArray(cv.box) || cv.box.length !== 4 || !cv.box.every(Number.isFinite)) {
    note.textContent = "No detection view is available for this asset.";
    return;
  }
  score.textContent = [cv.station, Number.isFinite(cv.score) ? `score ${cv.score}` : ""].filter(Boolean).join(" · ");
  note.textContent = cv.prompt ? `Detector prompt: ${cv.prompt} · Crop match: ${asset.label}` : `Crop match: ${asset.label}`;

  const renderImg = (img) => {
    canvas.hidden = false;
    ctx.clearRect(0, 0, W, H);
    ctx.drawImage(img, 0, 0, W, H);
    const [bx, by, bw, bh] = cv.box;
    const rx = bx * W;
    const ry = by * H;
    const rw = bw * W;
    const rh = bh * H;

    // Terracotta Red 2D detection bounding box (exactly like IMG_4398.HEIC)
    ctx.strokeStyle = "#d94838";
    ctx.lineWidth = 3.0;
    ctx.strokeRect(rx, ry, rw, rh);

    // Tag pill on top-left of 2D box
    const tagTxt = `${asset.tag} · ${asset.label}`;
    ctx.font = "bold 12px -apple-system, sans-serif";
    const tw = ctx.measureText(tagTxt).width + 12;
    ctx.fillStyle = "#d94838";
    ctx.fillRect(rx, Math.max(0, ry - 20), tw, 20);
    ctx.fillStyle = "#ffffff";
    ctx.fillText(tagTxt, rx + 6, Math.max(14, ry - 6));
  };

  if (imageCache.has(cv.image)) {
    renderImg(imageCache.get(cv.image));
  } else {
    const img = new Image();
    img.onload = () => {
      imageCache.set(cv.image, img);
      if (state.selectedAsset && state.selectedAsset.id === asset.id) {
        renderImg(img);
      }
    };
    img.onerror = () => { if (state.selectedAsset?.id === asset.id) note.textContent = "Detection image unavailable."; };
    img.src = cv.image;
  }
}

function fmtComma(num) {
  return Number(num).toFixed(3).replace(".", ",");
}

function selectAsset(asset, openInspector = false) {
  if (movePreview && movePreview.asset.id !== asset.id) closeMovePreview();
  asset = roomAsset(asset);
  if (removedIds.has(asset.id) && takeApart?.isOpen) takeApart.close();
  state.selectedAsset = asset;
  if (takeApart?.isOpen && takeApart.active.asset !== asset) takeApart.open(asset);
  copilotRequestId++;
  const presentation = assetPresentation(asset);
  const removed = removedIds.has(asset.id);
  renderRoomEditControls();
  document.getElementById("active-asset-details").hidden = removed;
  if (removed) {
    renderAssetPhoto(null,document.getElementById("asset-photo"),location.href);
    document.getElementById("detail-id-status").textContent = `${asset.id} · Removed from room`;
    document.getElementById("detail-health-pill").hidden = true;
    document.getElementById("detail-title").textContent = `${asset.label} · ${asset.tag}`;
    document.getElementById("detail-subtitle").textContent = presentation.category;
    renderProposalList();
    if (openInspector) setInspectorOpen(true);
    return;
  }
  const uninstrumented = presentation.kind !== "instrumented";
  renderAssetPhoto(asset,document.getElementById("asset-photo"),location.href);
  document.querySelector(".copilot-block").hidden = uninstrumented;
  document.getElementById("movable-notice").hidden = !uninstrumented;
  document.getElementById("movable-notice").textContent = presentation.notice;
  document.getElementById("movable-notice").className = `movable-notice ${presentation.kind}`;
  document.getElementById("btn-draft-wo").hidden = uninstrumented;
  renderProposalList();

  const navigateButton = document.getElementById("btn-navigate-selected");
  navigateButton.disabled = false;
  navigateButton.textContent = "Navigate here";
  if (openInspector) setInspectorOpen(true);

  document.getElementById("detail-id-status").textContent =
    `${asset.asset_id} · ${asset.id} / ${asset.condition_state}`;
  const hp = document.getElementById("detail-health-pill");
  hp.hidden = uninstrumented;
  hp.className = `health-pill ${asset.condition_state}`;
  hp.textContent = uninstrumented ? presentation.notice : `${asset.health_score == null ? "Movable object" : `Health ${asset.health_score}%`}`;

  document.getElementById("detail-title").textContent = `${asset.label} · ${asset.tag}`;
  document.getElementById("service-guidance-title").textContent = `Service guidance for ${asset.tag}`;
  document.getElementById("detail-subtitle").textContent =
    presentation.category;

  const effPct = Math.round((asset.effective_confidence || 0.8) * 100);
  const dqTxt = asset.dq_penalty ? ` (includes -${Math.round(asset.dq_penalty * 100)}% data quality penalty)` : "";
  document.getElementById("detail-match-note").textContent =
    `Original box: ${asset.label}. Best visual match: ${asset.label}. Effective AI confidence: ${effPct}%${dqTxt}.`;

  // 2D camera detection photo + red box
  drawDetectionCrop(asset);

  // Scan bounds (in comma-formatted scan units matching IMG_4398.HEIC)
  document.getElementById("detail-class-box").textContent = presentation.category;
  const sc = asset.scan_center || asset.center;
  document.getElementById("bound-cx").textContent = fmtComma(sc[0]);
  document.getElementById("bound-cy").textContent = fmtComma(sc[1]);
  document.getElementById("bound-cz").textContent = fmtComma(sc[2]);

  document.getElementById("bound-sx").textContent = fmtComma(asset.size[0]);
  document.getElementById("bound-sy").textContent = fmtComma(asset.size[1]);
  document.getElementById("bound-sz").textContent = fmtComma(asset.size[2]);

  if (uninstrumented) return;

  // Grounded AI Copilot
  document.getElementById("detail-doc-id").textContent = asset.knowledge_doc_id || "DOC-3D-MAPPING";
  const diag = asset.diagnostics || {};
  document.getElementById("exec-summary-box").textContent = diag.executive_summary;
  const severity = document.getElementById("triage-severity");
  const severityLabel = diag.ai_triage?.severity?.label;
  severity.hidden = !severityLabel;
  severity.textContent = severityLabel ? `AI severity · ${severityLabel}` : "";
  const safety = document.getElementById("arrival-safety");
  const safetyStep = diag.verification_steps?.find(step => step.citation === "DOC-FIELD-SAFETY");
  safety.hidden = !safetyStep;
  safety.textContent = safetyStep ? `LOTO first. ${safetyStep.detail} · ${safetyStep.citation}` : "";

  renderTelemetryChart(asset);

  document.getElementById("observations-list").innerHTML = (diag.observations || [])
    .map(
      (o) => `
      <div class="stack-card">
        <div class="stack-card-head">
          <span>${o.metric}</span>
          <span class="cite-tag">${o.source_id}</span>
        </div>
        <div>${o.value}</div>
      </div>
    `
    )
    .join("");

  document.getElementById("hypotheses-list").innerHTML = (diag.hypotheses || [])
    .map(
      (h) => `
      <div class="stack-card">
        <div class="stack-card-head">
          <span>#${h.rank} ${h.hypothesis}</span>
          <span>${Math.round(h.confidence * 100)}%</span>
        </div>
        <div style="color:#5a605d;">${h.rationale}</div>
        <div>
          ${(h.supporting_ids || []).map((id) => `<span class="cite-tag">${id}</span>`).join("")}
        </div>
      </div>
    `
    )
    .join("");

  document.getElementById("verification-steps-list").innerHTML = (diag.verification_steps || [])
    .map(
      (s) => `
      <div class="stack-card">
        <strong>${s.step}. ${s.title}:</strong> ${s.detail}
        <span class="cite-tag">${s.citation}</span>
      </div>
    `
    )
    .join("");

  document.getElementById("copilot-chat-output").textContent = diag.executive_summary;
}

function renderTelemetryChart(asset) {
  const canvas = document.getElementById("telemetry-chart");
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, W, H);

  const series = asset.telemetry?.series || {};
  let s1 = series.position_setpoint_pct || series.vibration_mm_s || series.process_pressure_bar || [];
  let s2 = series.position_actual_pct || series.bearing_temp_c || [];
  let label = "Live Telemetry";

  if (series.position_setpoint_pct && series.position_actual_pct) {
    label = "SP (forest) vs Actual PV (terracotta)";
  } else if (series.vibration_mm_s) {
    label = "Vibration mm/s (forest) & Temp (terracotta)";
  }
  document.getElementById("chart-metric-label").textContent = label;

  if (!s1 || s1.length < 2) {
    ctx.fillStyle = "#5a605d";
    ctx.font = "11px sans-serif";
    ctx.fillText(`Nominal: Pressure ${asset.telemetry?.latest?.process_pressure_bar ?? 4.8} bar · Signal 99.2%`, 10, H / 2);
    return;
  }

  const allVals = [...s1.filter((v) => v != null), ...s2.filter((v) => v != null)];
  const minV = Math.min(...allVals);
  const maxV = Math.max(...allVals, minV + 1e-3);

  const drawTrace = (arr, color) => {
    const valid = arr.filter((v) => v != null);
    if (valid.length < 2) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.0;
    ctx.beginPath();
    valid.forEach((v, i) => {
      const x = 8 + (i / (valid.length - 1)) * (W - 16);
      const y = H - 10 - ((v - minV) / (maxV - minV)) * (H - 20);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  };

  drawTrace(s1, "#204e39");
  if (s2 && s2.length > 1) drawTrace(s2, "#d94838");
}

function setInspectorOpen(open) {
  if (!open && movePreview) closeMovePreview();
  document.body.classList.toggle("inspector-open",open);
  document.getElementById("btn-inspector").setAttribute("aria-expanded",String(open));
}

function showArrivalReport(assetId) {
  const asset=roomAsset(state.assetsById.get(assetId));
  if(!asset)return;
  selectAsset(asset,true);
  document.getElementById("canvas-container").classList.remove("map-expanded");
  document.getElementById("btn-map-expand").textContent="Expand map";
  document.getElementById("btn-map-expand").setAttribute("aria-expanded","false");
  const presentation = assetPresentation(asset);
  const uninstrumented = presentation.kind !== "instrumented";
  document.getElementById("arrival-evidence").open=!uninstrumented;
  const heading=document.getElementById(uninstrumented ? "detail-title" : "service-guidance-title");
  document.querySelector(".inspector-scroll").scrollTop=0;
  heading.focus({preventScroll:true});
  heading.scrollIntoView({block:"start"});
  document.getElementById("arrival-announcement").textContent=uninstrumented ? `Arrived at ${asset.tag}. ${presentation.notice}.` : `Arrived at ${asset.tag}. Diagnosis, evidence, and inspection steps are open.`;
}

function applyJourneyEvent(event) {
  const result=transitionJourney(state.journey,event);
  state.journey=result.journey;
  if(result.arrivedAssetId)showArrivalReport(result.arrivedAssetId);
  renderJourneyControls(sampleRoute(state.journey.route,state.journey.elapsed));
}

function renderJourneyControls(nav) {
  const journey=state.journey,view=journeyView(journey),idle=journey.phase==="idle";
  const manual = !!state.manualWalk;
  const button=document.getElementById("btn-play");
  button.disabled=manual || view.disabled;
  button.textContent=view.button;
  document.getElementById("nav-hud-state").textContent=manual ? "MANUAL CONTROL" : view.status;
  document.getElementById("nav-hud-target").textContent=manual ? "Explore the room" : view.destination;
  document.querySelector(".journey-controls").hidden = manual;
  document.getElementById("btn-manual-walk").hidden = !manual;
  document.getElementById("btn-drop-obstacle").disabled = manual || !!state.demoObstacle || !["moving","paused"].includes(journey.phase) || journey.route.total_distance_m-nav.distance < 2;
  const remaining=Math.max(0,journey.route.total_distance_m-nav.distance);
  document.getElementById("nav-hud-metrics").textContent=manual ? "WASD to walk · ← → to turn · F to inspect nearby" : idle ? "Choose a map point, equipment, or room tour." : `${remaining.toFixed(1)} m remaining · ${view.remainingSeconds} s`;
  document.getElementById("sim-dist-label").textContent=idle?"":`${nav.distance.toFixed(1)} / ${journey.route.total_distance_m.toFixed(1)} m`;
  document.getElementById("navigation-message").textContent=view.error;
  document.getElementById("map-feedback").textContent=view.error;
}

function activateRoute(route, {preserveWalk=false, preserveHistory=false, keepView=false} = {}) {
  const result=transitionJourney(state.journey,{type:"start",route,position:robotPosition()});
  state.manualWalk = null;
  walkKeys.clear();
  if (!keepView) setInspectorOpen(false);
  document.getElementById("arrival-announcement").textContent="";
  state.journey=result.journey;
  if (!preserveWalk) state.walkSummary = {passed:new Set(),attention:new Set(),previous:robotPosition()};
  if (!preserveHistory) clearRouteHistory();
  recordPassedAssets(robotPosition());
  rebuildRouteGeometry();rebuildReachableOverlay();
  if (!keepView) applyCameraPreset(state.cameraPreset === "first" ? "first" : "walk");
  if(result.arrivedAssetId)showArrivalReport(result.arrivedAssetId);
  renderJourneyControls(sampleRoute(route,state.journey.elapsed));
}

function clearRouteHistory() {
  state.previousRoute = null;
  if (previousRouteLine) {
    scene.remove(previousRouteLine);
    previousRouteLine.geometry.dispose();previousRouteLine.material.dispose();previousRouteLine=null;
  }
  document.getElementById("replan-status").textContent="";
}

function fadePreviousRoute(route) {
  state.previousRoute=route;
  const points=route.waypoints.map(w=>new THREE.Vector3(w.position[0],.035,w.position[2]));
  previousRouteLine=new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),
    new THREE.LineBasicMaterial({color:0xc0c9cc,transparent:true,opacity:.28,depthWrite:false}));
  scene.add(previousRouteLine);
}

function dropDemoObstacle() {
  try {
    const route=state.journey.route;
    const result=planObstacleDrop(state.twin.navigation,route,robotPosition(),state.journey.elapsed);
    clearRouteHistory();
    fadePreviousRoute(route);
    state.demoObstacle=result.obstacle;
    state.twin.navigation=result.navigation;
    const o=result.obstacle;
    const box=new THREE.Mesh(new THREE.BoxGeometry(o.size,o.size,o.size),new THREE.MeshBasicMaterial({color:0xd99044}));
    box.position.set(o.x,o.size/2,o.z);scene.add(box);
    const outline=new THREE.LineSegments(new THREE.EdgesGeometry(box.geometry),new THREE.LineBasicMaterial({color:0xffdc9e}));
    box.add(outline);
    if (result.route) {
      activateRoute(result.route,{preserveWalk:true,preserveHistory:true});
      document.getElementById("replan-status").textContent="Obstacle dropped · route re-planned · old route faded";
    } else {
      rebuildReachableOverlay();
      navigationError(new Error("Obstacle dropped. No safe detour; robot stopped. Choose another destination."));
      document.getElementById("replan-status").textContent="Old route faded · obstacle blocks this passage";
    }
  } catch(error) {
    document.getElementById("replan-status").textContent=error.message;
  }
}

function canRemove(asset) {
  return !!asset && !asset.is_target && asset.asset_id !== state.twin.target_asset_id && !!instanceSizes.get(asset.id);
}

function roomAsset(asset) {
  if (!asset) return asset;
  const id = foldedRoomParts.get(asset.id) || asset.id;
  return liveRoomAssets.get(id) || state.assetsByObj.get(id) || asset;
}

function cellAtPosition({x,z}, navigation=state.twin.navigation) {
  const m=navigation.grid_meta;
  return navigation.walkable_grid[Math.floor((x-m.x_min)/m.res)]?.[Math.floor((z-m.z_min)/m.res)];
}

function prepareRoomEdits(edits, placements=roomPlacements) {
  const ids = new Set(edits.map(edit=>edit.id));
  const affected = new Set([...ids,...placements.keys()].sort());
  const reviews = new Map(), parts = new Map();
  for (const id of affected) {
    const review = reviewedTrolleyParts(state.assetsByObj.get(id),state.assetsByObj);
    if (!review) continue;
    const railOwner = !affected.has(review.rail.id) && instanceSizes.has(review.rail.id)
      ? instancesById.get(review.rail.id) : undefined;
    if (railOwner) parts.set(review.rail.id,id);
    reviews.set(id,{...review,railOwner});
  }
  const instances = new Set([...affected].map(id=>instancesById.get(id)));
  const assets = state.twin.assets.filter(asset=>!ids.has(asset.id) && !parts.has(asset.id))
    .map(asset=>placements.has(asset.id) ? placedRoomAsset(asset,placements.get(asset.id),reviews.get(asset.id)?.baseY) : asset);
  let navigation = initialNavigation;
  let fills = [], placed = [], owners = sourceScan?.[3];
  if (affected.size) {
    indexedScan ??= indexRoomScan(...sourceScan);
    owners = indexedScan.instances.slice();
    const added = new Map();
    const assign = (index,owner) => {
      owners[index]=owner;
      if (!owner) return;
      if (!added.has(owner)) added.set(owner,[]);
      added.get(owner).push(index);
    };
    for (const id of affected) {
      const asset = state.assetsByObj.get(id), owner = instancesById.get(id);
      const review = reviews.get(id);
      const traceKey = `${id}:${review?.railOwner ?? ''}`;
      if (!tracedFragments.has(traceKey)) tracedFragments.set(traceKey,
        traceRoomInstance(indexedScan,asset,owner,state.twin.assets,state.twin.scene.walls,review));
      const {recovered,retained}=tracedFragments.get(traceKey);
      if (review?.railOwner) for (const index of indexedScan.owned.get(review.railOwner)) assign(index,owner);
      for (const index of recovered) if (!owners[index]) assign(index,owner);
      for (const [index,neighbour] of retained) assign(index,neighbour);
    }
    const scan = {...indexedScan,instances:owners};
    fills = [...affected].map(id=>{
      const asset = state.assetsByObj.get(id);
      return [asset,fillRoomObject(scan,asset,instances,state.twin.scene.walls)];
    });
    placed = assets.filter(asset=>asset.transform).map(asset=>{
      const owner = instancesById.get(asset.id);
      const indices=[...indexedScan.owned.get(owner).filter(index=>owners[index]===owner),...(added.get(owner)||[])];
      return copyPlacedSplats(scan,indices,asset,owner);
    });
    navigation = navigationAfterRoomEdits(originalNavigation,assets,scan,instances,fills,walkPoses,initialNavigation.grid_meta,placed);
  }
  if (navigation && state.demoObstacle) navigation = blockBox(navigation,state.demoObstacle);
  return {ids,parts,instances,assets,navigation,fills,placed,owners};
}

function uploadRoomFill(instances, layers, owners) {
  const u = splatMaterial.uniforms;
  if (owners && u.uInstTex.value.image.data !== owners) {
    u.uInstTex.value.image.data=owners;
    u.uInstTex.value.needsUpdate=true;
  }
  const mask = u.uRemoved.value.image.data;
  mask.fill(0);
  for (const index of instances) mask[index]=1;
  u.uRemoved.value.needsUpdate=true;
  u.uHasRemoved.value=Number(instances.size>0);
  const count=layers.reduce((sum,data)=>sum+data.length/12,0);
  const cols=Math.max(1,Math.min(256,count)), height=Math.max(1,Math.ceil(count/cols));
  const pixels=new Float32Array(cols*height*12), positions=new Float32Array(count*3);
  let offset=0;
  for(const data of layers){
    pixels.set(data,offset*12);
    for(let k=0;k<data.length;k+=12){
      positions.set(data.slice(k,k+3),offset*3);
      offset++;
    }
  }
  u.uRoomFill.value.dispose();
  u.uRoomFill.value=makeRoomTexture(pixels,cols*3,height);
  u.uRoomFillSize.value.set(cols*3,height);
  u.uFillCount.value=count;
  updateFillSort(positions);
}

function applyRoomEdits(snapshot, initial=false, prepared) {
  const prior = state.journey;
  const {ids,parts,instances,assets,navigation,fills,placed,owners} = prepared ?? prepareRoomEdits(snapshot.removed);
  if (!navigation) throw new Error("This room change would block the room entrance.");
  activeEdits = snapshot.removed;
  editsOffline = snapshot.offline;
  if (editsOffline || roomPlacements.size) document.getElementById('room-changes').open=true;
  removedIds = ids;
  foldedRoomParts = parts;
  state.roomAssets = assets;
  if (bimView?.active) bimView.setRoom(assets,state.twin.scene.walls,navigation.grid_meta);
  liveRoomAssets = new Map(assets.map(asset=>[asset.id,asset]));
  state.twin.navigation = navigation;
  equipmentObstacles = manualObstacles(assets,navigation.grid_meta.robot_radius_m);
  if (instances.size || !initial) uploadRoomFill(instances,[...fills.map(([,fill])=>fill.data),...placed],owners);
  for (const [id,entry] of boxMeshes) {
    entry.removed=ids.has(id) || parts.has(id);
    const source=state.assetsByObj.get(id);
    entry.asset=liveRoomAssets.get(id) || source;
    entry.group.rotation.y=-entry.asset.yaw;
    entry.group.scale.y=source.size[1] > 0 ? entry.asset.size[1]/source.size[1] : 1;
    if (entry.removed) entry.group.visible=false;
  }
  if (state.walkSummary) for (const id of [...ids,...parts.keys()]) {
    state.walkSummary.passed.delete(id);
    state.walkSummary.attention.delete(id);
  }
  if (takeApart?.isOpen && (ids.has(takeApart.active.asset.id) || roomPlacements.has(takeApart.active.asset.id))) takeApart.close();
  hudLastUpdate=-1;
  updateSidebarStats();
  renderRoomEditControls();
  if (initial) return;
  selectAsset(state.selectedAsset);
  rebuildReachableOverlay();
  replanAfterRoomEdit(prior);
}

function replanAfterRoomEdit(prior) {
  if (!['moving','paused','blocked'].includes(prior.phase) || state.manualWalk || prior.route.kind==='idle') return;
  const {route,elapsed}=prior;
  const start=robotPosition(), navigation=state.twin.navigation;
  const asset=route.asset_id && roomAsset(state.assetsById.get(route.asset_id));
  clearRouteHistory();
  if (asset && removedIds.has(asset.id)) {
    state.journey=createJourney({kind:"idle",tag:"Explore the room",duration_s:0,total_distance_m:0,
      waypoints:[{position:[start.x,0,start.z],t:0,distance_m:0,yaw:robotGroup.rotation.y}]});
    rebuildRouteGeometry();
    navigationError(new Error(`${asset.tag} was removed. Choose another destination.`));
    return;
  }
  try {
    let replacement;
    const destination=asset && !asset.is_target ? assetDestination(navigation,start,asset) : {...route,stops:[]};
    try { replacement=planFromPosition(navigation,start,destination); }
    catch(error) {
      if (!asset) throw error;
      replacement=planFromPosition(navigation,start,assetDestination(navigation,start,asset));
    }
    const progress=sampleRoute(route,elapsed).distance;
    const shorter=replacement.total_distance_m+.02<route.total_distance_m-progress;
    if (shorter) fadePreviousRoute({...route,waypoints:[{position:[start.x,0,start.z]},...route.waypoints.filter(w=>w.distance_m>progress)]});
    activateRoute(replacement,{preserveWalk:true,preserveHistory:true,keepView:true});
    if (prior.phase==='paused') applyJourneyEvent({type:'pause'});
    document.getElementById('replan-status').textContent=shorter ? 'Shorter route · previous route faded' : 'Route updated for the room change';
  } catch(error) {
    rebuildRouteGeometry();
    navigationError(new Error(`Room changed. ${error.message}`));
  }
}

function renderRoomEditControls() {
  const count=document.getElementById('room-object-count');
  const moved = [...roomPlacements.keys()].filter(id=>!removedIds.has(id));
  count.textContent=`${state.twin.assets.length} objects · ${removedIds.size} removed${moved.length ? ` · ${moved.length} moved` : ''}`;
  document.getElementById('room-change-count').textContent=String(removedIds.size+moved.length);
  const status=document.getElementById('room-edit-status');
  status.textContent=[editsOffline && 'edits not saved to server',moved.length && 'Moved positions are saved in this browser.'].filter(Boolean).join(' · ');
  status.hidden=!status.textContent;
  const asset=state.selectedAsset;
  const removed=removedIds.has(asset.id);
  const button=document.getElementById('btn-room-edit');
  button.textContent=removed ? 'Restore' : 'Remove from room';
  button.disabled=editBusy || !canRemove(asset);
  const tooltip=asset.is_target || asset.asset_id===state.twin.target_asset_id ?
    `${asset.tag} is the current inspection target.` : !canRemove(asset) ? 'Scan membership is unavailable for this object.' : '';
  button.title=tooltip;
  document.getElementById('room-edit-tooltip').title=tooltip;
  const move=document.getElementById('btn-move-object');
  const reset=document.getElementById('btn-reset-position');
  move.disabled=editBusy || removed || !canRemove(asset);
  move.title=tooltip;
  reset.hidden=removed || !roomPlacements.has(asset.id);
  reset.disabled=editBusy;
  const notice=document.getElementById('room-edit-notice');
  notice.textContent=removed ? (asset.mapped ? `${asset.tag} removed: its Excel record is no longer located in this room` : 'Removed from this room.') :
    roomPlacements.has(asset.id) ? `Moved to X ${asset.center[0].toFixed(2)} m, Z ${asset.center[2].toFixed(2)} m. The photo shows the original location.` : '';
  notice.hidden=!notice.textContent;
  const list=document.getElementById('room-edit-list');
  list.replaceChildren();
  for(const edit of activeEdits){
    const item=state.assetsByObj.get(edit.id), row=document.createElement('div');
    row.className='room-change-row';
    const name=document.createElement('button'), time=document.createElement('time'), restore=document.createElement('button');
    name.type=restore.type='button';
    name.textContent=item.tag;
    name.title=item.label;
    name.addEventListener('click',()=>selectAsset(item,true));
    const at=new Date(edit.at);
    if (Number.isFinite(at.valueOf())) {
      time.dateTime=at.toISOString();
      time.title=at.toLocaleString();
      time.textContent=at.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    }
    restore.textContent='Restore';
    restore.setAttribute('aria-label',`Restore ${item.tag}`);
    restore.disabled=editBusy;
    restore.addEventListener('click',()=>changeRoom('restore',item));
    row.append(name,time,restore);
    list.append(row);
  }
  for (const id of moved) {
    const item=liveRoomAssets.get(id), row=document.createElement('div');
    row.className='room-change-row';
    const name=document.createElement('button'), reset=document.createElement('button');
    name.type=reset.type='button';
    name.textContent=item.tag;
    name.title=item.label;
    name.addEventListener('click',()=>selectAsset(item,true));
    reset.textContent='Reset position';
    reset.setAttribute('aria-label',`Reset position of ${item.tag}`);
    reset.disabled=editBusy;
    reset.addEventListener('click',()=>changePlacement(item,null));
    row.append(name,reset);
    list.append(row);
  }
}

async function changeRoom(action,asset) {
  if (editBusy || !canRemove(asset)) return;
  closeMovePreview();
  editBusy=true;
  walkKeys.clear();
  renderRoomEditControls();
  try {
    const next=activeEdits.filter(edit=>edit.id!==asset.id);
    if(action==='remove') next.push({id:asset.id});
    if (action==='restore') {
      const position=roomPlacements.get(asset.id);
      const source=state.assetsByObj.get(asset.id);
      const review=reviewedTrolleyParts(source,state.assetsByObj);
      const restored=position ? placedRoomAsset(source,position,review?.baseY) : asset;
      if (state.roomAssets.some(other=>other.transform && roomBoundsOverlap(restored,other))) {
        throw new Error('Move the other object away from this location first.');
      }
    }
    const prepared=prepareRoomEdits(next);
    if (!prepared.navigation || !cellAtPosition(robotPosition(),prepared.navigation)) {
      throw new Error('Move the robot clear of this location before changing the room.');
    }
    const snapshot=await editStore.edit(action,asset.id);
    const same=snapshot.removed.length===prepared.ids.size && snapshot.removed.every(edit=>prepared.ids.has(edit.id));
    applyRoomEdits(snapshot,false,same ? prepared : undefined);
    document.getElementById('room-changes').open=true;
  } catch(error) {
    const notice=document.getElementById('room-edit-notice');
    notice.textContent=error.message;
    notice.hidden=false;
  } finally {
    editBusy=false;
    document.getElementById('btn-room-edit').disabled=!canRemove(state.selectedAsset);
    document.getElementById('btn-move-object').disabled=removedIds.has(state.selectedAsset.id) || !canRemove(state.selectedAsset);
    document.getElementById('btn-reset-position').disabled=false;
    document.querySelectorAll('#room-edit-list button[aria-label]').forEach(button=>button.disabled=false);
  }
}

function viewRoomLocation(asset) {
  if (takeApart?.isOpen) takeApart.close();
  const navigation=state.twin.navigation,m=navigation.grid_meta;
  const [x,y,z]=asset.center, eye=1.22;
  let best, cost=Infinity;
  for(const p of reachableFloor(navigation,robotPosition())) {
    const distance=Math.hypot(p.x-x,eye-y,p.z-z);
    if(distance<1.75 || distance>2.25) continue;
    let obstruction=0;
    for(let t=.05;t<.78;t+=.04){
      const i=Math.floor((p.x+(x-p.x)*t-m.x_min)/m.res),j=Math.floor((p.z+(z-p.z)*t-m.z_min)/m.res);
      if(!navigation.clearance_grid_m[i]?.[j]) obstruction++;
    }
    const score=Math.abs(distance-2)*4+obstruction*.16-p.clearance*.35;
    if(score<cost){best=p;cost=score;}
  }
  applyCameraPreset('detail');
  if(best) {
    camera.position.set(best.x,eye,best.z);
    controls.target.fromArray(asset.center);
    controls.update();
  }
}

function moveField(name) {
  return document.getElementById(`room-move-${name}`);
}

function beginMovePreview(asset) {
  if (editBusy || removedIds.has(asset.id) || !canRemove(asset)) return;
  closeMovePreview();
  walkKeys.clear();
  viewRoomLocation(asset);
  const source=state.assetsByObj.get(asset.id);
  const review=reviewedTrolleyParts(source,state.assetsByObj);
  const [sx,sy,sz]=source.size;
  const baseY=review?.baseY ?? source.center[1]-sy/2;
  const box = new THREE.BoxGeometry(sx,source.center[1]+sy/2-baseY,sz);
  const edges = new THREE.EdgesGeometry(box);
  box.dispose();
  const outline = new THREE.LineSegments(edges,new THREE.LineBasicMaterial({color:0x79e2ef,depthTest:false}));
  outline.renderOrder=10;
  scene.add(outline);
  movePreview={asset:source,outline,review};
  document.body.classList.add('placing-object');
  document.getElementById('room-move-form').hidden=false;
  const [x,y,z]=asset.center;
  moveField('x').value=x.toFixed(2);
  moveField('z').value=z.toFixed(2);
  moveField('height').value=Math.max(0,asset.transform ? y-asset.size[1]/2 : baseY).toFixed(2);
  moveField('yaw').value=String(Math.round(asset.yaw*180/Math.PI));
  updateMovePreview();
}

function closeMovePreview() {
  if (!movePreview) return;
  scene.remove(movePreview.outline);
  movePreview.outline.geometry.dispose();
  movePreview.outline.material.dispose();
  movePreview=null;
  document.body.classList.remove('placing-object');
  document.getElementById('room-move-form').hidden=true;
}

function setMovePoint(point) {
  moveField('x').value=point.x.toFixed(2);
  moveField('z').value=point.z.toFixed(2);
  updateMovePreview();
}

function updateMovePreview() {
  if (!movePreview) return;
  const {asset,outline,review}=movePreview;
  const [x,z,height,degrees]=['x','z','height','yaw'].map(name=>moveField(name).valueAsNumber);
  const valid=[x,z,height,degrees].every(Number.isFinite);
  const placement=valid ? {center:[x,height+asset.size[1]/2,z],yaw:degrees*Math.PI/180,source:[...asset.center,asset.yaw]} : null;
  let error='Enter a position, height, and rotation.';
  outline.visible=valid;
  if (placement) {
    indexedScan ??= indexRoomScan(...sourceScan);
    const omitted=new Set([...removedIds,...roomPlacements.keys(),asset.id].map(id=>instancesById.get(id)));
    const rail=review?.rail;
    const includeRail=rail && !removedIds.has(rail.id) && !roomPlacements.has(rail.id);
    if (includeRail) omitted.add(instancesById.get(rail.id));
    const collisionAssets=includeRail ? state.roomAssets.filter(other=>other.id!==rail.id) : state.roomAssets;
    const placedAsset=placedRoomAsset(asset,placement,review?.baseY);
    error=roomPlacementError(placedAsset,collisionAssets,indexedScan,omitted,initialNavigation.grid_meta);
    outline.position.fromArray(placedAsset.center);
    outline.rotation.y=-placement.yaw;
    outline.material.color.setHex(error ? 0xff9b85 : 0x79e2ef);
  }
  Object.assign(movePreview,{placement,error});
  document.getElementById('room-move-error').textContent=error;
  document.getElementById('btn-place-object').disabled=!!error;
}

function changePlacement(asset, placement) {
  if (editBusy || removedIds.has(asset.id) || !canRemove(asset)) return;
  const next=new Map(roomPlacements);
  if (placement) next.set(asset.id,placement);
  else next.delete(asset.id);
  try {
    const original=state.assetsByObj.get(asset.id);
    if (!placement && state.roomAssets.some(other=>other.transform && other.id!==asset.id && roomBoundsOverlap(original,other))) {
      throw new Error('Move the other object away from this location first.');
    }
    const prepared=prepareRoomEdits(activeEdits,next);
    if (!prepared.navigation || !cellAtPosition(robotPosition(),prepared.navigation)) {
      throw new Error('Move the robot clear of this location before placing the object.');
    }
    saveRoomPlacements(next);
    roomPlacements=next;
    closeMovePreview();
    applyRoomEdits({removed:activeEdits,offline:editsOffline},false,prepared);
    if (state.selectedAsset.id===asset.id) viewRoomLocation(state.selectedAsset);
  } catch(error) {
    if (movePreview) document.getElementById('room-move-error').textContent=error.message;
    else {
      const notice=document.getElementById('room-edit-notice');
      notice.textContent=error.message;
      notice.hidden=false;
    }
  }
}

function bindRoomEdits() {
  const dialog=document.getElementById('confirm-room-removal');
  let pendingAsset;
  document.getElementById('btn-room-edit').addEventListener('click',()=>{
    const asset=state.selectedAsset;
    if (!canRemove(asset) || editBusy) return;
    if (removedIds.has(asset.id)) { changeRoom('restore',asset); return; }
    pendingAsset=asset;
    dialog.returnValue='';
    document.getElementById('room-remove-message').textContent=`Remove ${asset.tag} · ${asset.label} from this room? You can restore it from Room changes.`;
    dialog.showModal();
  });
  dialog.addEventListener('close',()=>{
    if(dialog.returnValue==='remove' && pendingAsset) changeRoom('remove',pendingAsset);
    pendingAsset=null;
  });
  document.getElementById('btn-room-location').addEventListener('click',()=>viewRoomLocation(state.selectedAsset));
  document.getElementById('btn-move-object').addEventListener('click',()=>beginMovePreview(state.selectedAsset));
  document.getElementById('btn-reset-position').addEventListener('click',()=>changePlacement(state.selectedAsset,null));
  document.getElementById('btn-cancel-move').addEventListener('click',closeMovePreview);
  const form=document.getElementById('room-move-form');
  form.addEventListener('input',updateMovePreview);
  form.addEventListener('submit',event=>{
    event.preventDefault();
    if (movePreview?.placement && !movePreview.error) changePlacement(movePreview.asset,movePreview.placement);
  });
  document.getElementById('room-fill-debug').addEventListener('change',event=>{
    splatMaterial.uniforms.uFillDebug.value=Number(event.target.checked);
  });
}

function robotPosition() {return {x:robotGroup.position.x,z:robotGroup.position.z};}

function cameraYaw() {
  const direction = camera.getWorldDirection(new THREE.Vector3());
  return Math.atan2(direction.x, direction.z);
}

function beginManualWalk() {
  const position = robotPosition(), yaw = cameraYaw();
  state.manualWalk = {yaw};
  state.journey = createJourney({kind:"idle", tag:"Explore the room", duration_s:0, total_distance_m:0,
    waypoints:[{position:[position.x, 0, position.z], t:0, distance_m:0, yaw}]});
  state.walkSummary ??= {passed:new Set(), attention:new Set(), previous:position};
  document.getElementById("arrival-announcement").textContent = "";
  clearRouteHistory();
  rebuildRouteGeometry();
  applyCameraPreset(state.cameraPreset === "first" ? "first" : "walk");
}

function keyboardWalk(dt) {
  const manual = state.manualWalk;
  const forward = Number(walkKeys.has("w") || walkKeys.has("arrowup")) - Number(walkKeys.has("s") || walkKeys.has("arrowdown"));
  const side = Number(walkKeys.has("d")) - Number(walkKeys.has("a"));
  const turn = Number(walkKeys.has("arrowleft")) - Number(walkKeys.has("arrowright"));
  if (!["walk", "first"].includes(state.cameraPreset)) {
    manual.yaw = cameraYaw();
    if (turn) applyCameraPreset("walk");
  }
  manual.yaw += turn * 1.8 * dt;
  const x = Math.sin(manual.yaw), z = Math.cos(manual.yaw);
  const start = robotPosition();
  const step = dt / (Math.hypot(forward, side) || 1);
  const end = manualStep(state.twin.navigation, equipmentObstacles, start,
    (x * forward - z * side) * step, (z * forward + x * side) * step);
  const dx = end.x - start.x, dz = end.z - start.z;
  const yaw = state.cameraPreset === "first" ? manual.yaw :
    dx || dz ? Math.atan2(dx, dz) : robotGroup.rotation.y;
  return {...end, yaw, distance:0};
}

function updateWalkCamera(nav) {
  const yaw = state.manualWalk?.yaw ?? nav.yaw;
  const dx = Math.sin(yaw), dz = Math.cos(yaw);
  if (state.cameraPreset === "first") {
    camera.position.set(nav.x, .69, nav.z);
    controls.target.set(nav.x + dx, .69, nav.z + dz);
  } else {
    // Keep the trailing camera on this side of a wall when space behind the robot is tight.
    const start = [nav.x, 0, nav.z];
    let distance = 1.55;
    while (distance > .1 && !segmentIsFree(state.twin.navigation, start, [nav.x - dx * distance, 0, nav.z - dz * distance])) {
      distance *= .5;
    }
    if (distance <= .1) distance = 0;
    camera.position.set(nav.x - dx * distance, 1.4, nav.z - dz * distance);
    const lookAhead = .14 + .2 * distance;
    controls.target.set(nav.x + dx * lookAhead, .47, nav.z + dz * lookAhead);
  }
  camera.lookAt(controls.target);
}

function navigationError(error) {
  applyJourneyEvent({type:"block",message:error.message});
}

function dispatchRobotToAsset(asset) {
  asset=roomAsset(asset);
  if (!asset || removedIds.has(asset.id)) return;
  takeApart?.close();
  try {
    const start=robotPosition();
    const navigation = state.twin.navigation;
    const primary = navigation.primary_route;
    let route;
    if (asset.asset_id === state.twin.target_asset_id && primary?.validated && primary.asset_id === asset.asset_id) {
      try {
        route = planFromPosition(navigation, start, {...primary, kind:"asset"});
      } catch {
        // A newly blocked stand-off can still have another reachable inspection position.
      }
    }
    route ??= planFromPosition(navigation, start, assetDestination(navigation, start, asset));
    selectAsset(asset,false);
    activateRoute(route);
  } catch(error) {navigationError(error);}
}

function registerActivity() {
  attract.lastActivity=performance.now();
  if (!attract.active) return;
  attract.active=false;
  document.getElementById("autoplay-chip").hidden=true;
  if (state.journey.phase === "moving") applyJourneyEvent({type:"pause"});
}

function updateAttractMode(now) {
  if (bimView?.active) return;
  if (movePreview) return;
  if (takeApart?.isOpen || state.manualWalk || !attract.enabled || attract.active || document.hidden || now-attract.lastActivity < 20000) return;
  if (!["idle","arrived"].includes(state.journey.phase)) return;
  const target=state.assetsById.get(state.twin.target_asset_id);
  if (!target) return;
  dispatchRobotToAsset(target);
  attract.lastActivity=now;
  if (["moving","arrived"].includes(state.journey.phase)) {
    attract.active=true;
    document.getElementById("autoplay-chip").hidden=false;
  }
}

function bindUIEvents() {
  bindArView();
  bindRoomEdits();
  bimView=createBimRoom({camera,controls,container:document.getElementById("canvas-container")});
  semanticPlan=bindSemanticPlan({
    snapshot:()=>({assets:state.roomAssets,walls:state.twin.scene.walls,navigation:state.twin.navigation,
      robot:robotPosition(),journey:state.journey,selected:state.selectedAsset}),
    inspect:asset=>selectAsset(asset,true),
    navigate:dispatchRobotToAsset,
    viewModel:()=>applyCameraPreset("bim"),
  });
  document.getElementById("btn-semantic-plan").disabled=false;
  const resumeMission = () => dispatchRobotToAsset(state.assetsById.get(state.twin.target_asset_id));
  document.getElementById("btn-manual-walk").addEventListener("click", resumeMission);
  window.addEventListener("keydown", event => {
    if (movePreview) {
      if (event.key === 'Escape') closeMovePreview();
      return;
    }
    if (event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey ||
        event.target.closest?.("input, textarea, select, [contenteditable], video") ||
        document.querySelector("dialog[open], #wo-modal:not(.hidden)")) return;
    if (takeApart?.isOpen) return; // its own keys: R, G, Esc
    const key = event.key.toLowerCase();
    if (!event.repeat && key === "t" && state.selectedAsset && !removedIds.has(state.selectedAsset.id)) {
      event.preventDefault();
      takeApart.open(state.selectedAsset);
      return;
    }
    if (movementKeys.includes(key)) {
      event.preventDefault();
      if (!state.manualWalk && event.repeat) return;
      if (!state.manualWalk) beginManualWalk();
      walkKeys.add(key);
    } else if (!event.repeat && key === "m") {
      resumeMission();
    } else if (!event.repeat && key === "f") {
      const {x, z} = robotPosition();
      const nearest = nearbyAssets(state.roomAssets, x, z, 1.8)
        .filter(asset => assetPresentation(asset).kind === "instrumented")
        .sort((a, b) => a.distance_m - b.distance_m)[0];
      if (nearest) selectAsset(state.assetsByObj.get(nearest.id), true);
    }
  });
  window.addEventListener("keyup", event => walkKeys.delete(event.key.toLowerCase()));
  window.addEventListener("blur", () => walkKeys.clear());
  document.addEventListener("visibilitychange", () => walkKeys.clear());
  attract.lastActivity=performance.now();
  for (const type of ["pointerdown","keydown","wheel","touchstart"]) document.addEventListener(type,registerActivity,{capture:true,passive:true});
  document.addEventListener("pointermove",()=>{if (!attract.active) attract.lastActivity=performance.now();},{passive:true});
  document.addEventListener("visibilitychange",registerActivity);
  document.getElementById("autoplay-chip").addEventListener("click",registerActivity);
  document.getElementById("btn-drop-obstacle").addEventListener("click",dropDemoObstacle);
  for(const id of ["btn-room-tour","btn-go-target"])document.getElementById(id).disabled=false;
  document.getElementById("btn-room-tour").addEventListener("click",()=>{
    try{activateRoute(planRoomTour(state.twin.navigation,robotPosition()));}catch(error){navigationError(error);}
  });
  document.getElementById("route-map").addEventListener("click",e=>{
    const rect=e.currentTarget.getBoundingClientRect(),m=state.twin.navigation.grid_meta;
    const point=mapWorldPoint(m,e.currentTarget.width,e.currentTarget.height,
      (e.clientX-rect.left)/rect.width*e.currentTarget.width,
      (e.clientY-rect.top)/rect.height*e.currentTarget.height);
    if (movePreview) {
      if (point) setMovePoint(point);
      return;
    }
    if(!point){navigationError(new Error("Choose a point inside the room walls."));return;}
    const objects=state.roomAssets.filter(asset=>{
      if(asset.tier!=="equipment"||asset.center[1]-asset.size[1]/2>1.62||asset.center[1]+asset.size[1]/2<.10)return false;
      const dx=point.x-asset.center[0],dz=point.z-asset.center[2],c=Math.cos(asset.yaw),s=Math.sin(asset.yaw);
      return Math.abs(dx*c+dz*s)<=asset.size[0]/2 && Math.abs(-dx*s+dz*c)<=asset.size[2]/2;
    }).sort((a,b)=>Number(b.mapped)-Number(a.mapped)||a.size[0]*a.size[2]-b.size[0]*b.size[2]);
    if(objects.length){dispatchRobotToAsset(objects[0]);return;}
    try{
      const start=robotPosition();
      activateRoute(planFromPosition(state.twin.navigation,start,floorDestination(state.twin.navigation,start,point)));
    }catch(error){navigationError(error);}
  });
  document.getElementById("btn-close-inspector").addEventListener("click",()=>{
    setInspectorOpen(false);
  });
  document.getElementById("btn-map-expand").disabled = false;
  document.getElementById("btn-map-expand").addEventListener("click", e => {
    const open = document.getElementById("canvas-container").classList.toggle("map-expanded");
    e.target.textContent = open ? "Close map" : "Expand map";
    e.target.setAttribute("aria-expanded", String(open));
  });
  document.getElementById("btn-inspector").addEventListener("click", e => {
    setInspectorOpen(!document.body.classList.contains("inspector-open"));
  });
  document.getElementById("select-splat-mode").addEventListener("change", resizeRenderer);
  document.getElementById("select-visual-review").addEventListener("change", (e) => {
    state.visualReviewFilter = e.target.value;
    renderProposalList();
  });

  document.getElementById("asset-search").addEventListener("input", (e) => {
    state.searchQuery = e.target.value;
    renderProposalList();
  });

  for (const btn of document.querySelectorAll("[data-cam]")) {
    btn.addEventListener("click", () => applyCameraPreset(btn.dataset.cam));
  }

  for (const btn of document.querySelectorAll("[data-bmode]")) {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-bmode]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.boxesMode = btn.dataset.bmode;
    });
  }

  document.getElementById("btn-play").addEventListener("click", () => {
    applyJourneyEvent({type:state.journey.phase==="moving"?"pause":"resume"});
  });

  document.getElementById("btn-go-target").addEventListener("click", () => {
    const target = state.assetsById.get(state.twin.target_asset_id);
    if (target) dispatchRobotToAsset(target);
  });

  document.getElementById("btn-take-apart").addEventListener("click", () => takeApart.open(state.selectedAsset));
  document.getElementById("btn-navigate-selected").addEventListener("click", () => {
    if (state.selectedAsset) dispatchRobotToAsset(state.selectedAsset);
  });

  const askCopilot = async (question) => {
    const out = document.getElementById("copilot-chat-output");
    const asset=state.selectedAsset;
    const requestId=++copilotRequestId;
    const current=()=>requestId===copilotRequestId && state.selectedAsset?.asset_id===asset.asset_id;
    out.textContent = "Querying grounded telemetry, events, and Valmet SOP corpus...";
    try {
      const res = await fetch("/api/copilot/diagnose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({asset_id:asset.asset_id,question}),
      });
      if(!res.ok)throw new Error("Copilot request failed.");
      const data = await res.json();
      if(current())out.textContent = data.answer;
    } catch {
      if(current())out.textContent = asset.diagnostics?.executive_summary || "";
    }
  };

  for (const chip of document.querySelectorAll(".q-chip")) {
    chip.addEventListener("click", () => askCopilot(chip.dataset.q));
  }

  document.getElementById("copilot-chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const inp = document.getElementById("copilot-chat-input");
    if (inp.value.trim()) {
      askCopilot(inp.value.trim());
      inp.value = "";
    }
  });

  document.getElementById("btn-draft-wo").addEventListener("click", async () => {
    const asset = state.selectedAsset || state.assetsById.get(state.twin.target_asset_id);
    const res = await fetch("/api/work-orders/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_id: asset.asset_id }),
    });
    const data = await res.json();
    const d = data.draft || asset.diagnostics?.work_order_draft;
    const modal = document.getElementById("wo-modal");
    document.getElementById("wo-modal-title").textContent = `Work order draft · ${d?.draft_id || "WO-DRAFT"}`;
    document.getElementById("wo-modal-body").innerHTML = `
      <p><strong>Equipment:</strong> ${asset.tag} (${asset.asset_id}) — ${asset.label} (${asset.area_name})</p>
      <p><strong>Priority:</strong> ${d?.priority?.toUpperCase() || "HIGH"} | <strong>Action:</strong> ${d?.recommended_action || asset.recommended_next_check}</p>
      <p><strong>Summary:</strong> ${d?.summary || asset.issue_summary}</p>
      <p><strong>Attached Evidence IDs:</strong> <code>${(d?.evidence_bundle || []).join(", ")}</code></p>
      <p style="margin-top:8px;color:#204e39;font-weight:600;">${d?.confidence_note || ""}</p>
    `;
    modal.classList.remove("hidden");
  });

  document.getElementById("wo-modal-close").addEventListener("click", () => {
    document.getElementById("wo-modal").classList.add("hidden");
  });
}

async function bindArView() {
  const button = document.getElementById("btn-ar-view");
  const dialog = document.getElementById("ar-view");
  const video = dialog.querySelector("video");
  const excerpt = document.getElementById("btn-ar-target");
  const status = document.getElementById("ar-view-status");
  const targetTime = state.assetsById.get(state.twin.target_asset_id)?.photo_source?.video_t_s;
  try {
    const listing = await fetch("./data/", {cache:"no-store"});
    if (!listing.ok) return;
    const directory = new DOMParser().parseFromString(await listing.text(), "text/html");
    if (!directory.querySelector('a[href="overlay_walk.mp4"]')) return;
  } catch {
    return;
  }

  let videoLoad;
  button.hidden = false;
  button.addEventListener("click", async () => {
    dialog.showModal();
    if (!video.getAttribute("src")) {
      status.textContent = "Loading walkthrough…";
      status.hidden = false;
      // Buffer the local clip so seeking also works on servers without byte-range support.
      videoLoad ??= fetch("./data/overlay_walk.mp4").then(async response => {
        if (!response.ok) throw new Error("Walkthrough unavailable");
        video.src = URL.createObjectURL(await response.blob());
      });
      try {
        await videoLoad;
        status.hidden = true;
      } catch {
        videoLoad = null;
        status.textContent = "Could not load the walkthrough. Close and reopen to retry.";
        return;
      }
    }
    if (dialog.open) video.play().catch(() => {});
  });
  video.addEventListener("loadedmetadata", () => {
    excerpt.hidden = !Number.isFinite(targetTime) || targetTime < 0 || targetTime > video.duration;
  });
  excerpt.addEventListener("click", () => {
    video.currentTime = Math.max(0, Math.min(targetTime + 1, video.duration) - 25);
    video.play().catch(() => {});
  });
  document.getElementById("btn-close-ar").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => video.pause());
}

init();
