import * as THREE from "three";
import { inspectionCameraPose } from "./inspection-camera.mjs";
import { assetPresentation } from "./asset-presentation.mjs";
import { renderAssetPhoto } from "./asset-photo.mjs";
import { planObstacleDrop } from "./temporary-obstacles.mjs";
import { resolveRenderQuality } from "./render-quality.mjs";
import { createJourney, transitionJourney, journeyView } from "./journey.mjs";
import { createGameMap, mapFrame, mapPoint, mapWorldPoint } from "./game-map.mjs";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { explodeState, explodeOffset, loadInstanceUniforms, applyExplode, mountExplodeControls, SHADER_DECL, SHADER_OFFSET, SHADER_COLOR } from "./explode.mjs";
import { sampleRoute, nearbyAssets, planFromPosition, reachableFloor, floorDestination, assetDestination, planRoomTour, advanceAlongRoute } from "./navigation-view.mjs";

const state = {
  twin: null,
  assetsById: new Map(),
  assetsByObj: new Map(),
  selectedAsset: null,
  journey: null,
  walkSummary: null,
  previousRoute: null,
  demoObstacle: null,
  speed: 1.0,
  splatMode: "ewa",        // "ewa" | "semantic"
  cameraPreset: "walk",  // "inside" | "overview" | "walk" | "detail"
  boxesMode: "proximity",  // "proximity" | "all" | "off"
  visualReviewFilter: "mapped",
  classFilter: "all",
  searchQuery: "",
};

let scene, camera, renderer, controls;
let maxRenderDimension;
let splatMesh, splatMaterial, sortWorker;
let sortBusy = false;
let splatCount = 0;
let lastSortCamKey = "";
let hudLastUpdate = -1;
let proximityKey = "";
let mapBackground;

let currentNavigation;
let copilotRequestId=0;
let pathLineGroup, robotGroup, lidarRing, proxRing;
let previousRouteLine;
const boxMeshes = new Map();
const hudElements = new Map();
const imageCache = new Map();
const attract = {enabled:new URLSearchParams(location.search).get("attract") !== "0",active:false,lastActivity:0};

async function init() {
  const res = await fetch("./data/digital_twin.json");
  state.twin = await res.json();

  for (const a of state.twin.assets) {
    state.assetsById.set(a.asset_id, a);
    state.assetsByObj.set(a.id, a);
  }

  const first = state.twin.navigation.primary_route.waypoints[0];
  state.journey = createJourney({asset_id:null,tag:"Explore the room",kind:"idle",duration_s:0,total_distance_m:0,waypoints:[first],validated:true});
  const targetAsset = state.assetsById.get(state.twin.target_asset_id) || state.twin.assets[0];
  state.selectedAsset = targetAsset;

  updateSidebarStats();
  initThreeScene();
  await loadEwaGaussianSplats("./data/boiler_room_points.bin");
  buildAssetBoundingBoxes();
  mountExplodeControls();
  buildRobotAvatar();
  buildRouteMap();
  rebuildRouteGeometry();

  bindUIEvents();
  renderProposalList();
  selectAsset(targetAsset, false);
  applyCameraPreset("overview");

  let lastTs = performance.now();
  function animate(now) {
    requestAnimationFrame(animate);
    const dt = Math.min(0.08, (now - lastTs) / 1000);
    lastTs = now;
    updateAttractMode(now);
    stepSimulation(dt, now * 0.001);
    maybeTriggerDepthSort();
    renderer.render(scene, camera);
    updateHudLabels();
  }
  requestAnimationFrame(animate);
}

function updateSidebarStats() {
  const assets = state.twin.assets;
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
  splatCount = count;

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

  const initialOrder = new Float32Array(count);
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

      void main() {
        float col = mod(splatIndex, uTexSize.x);
        float row = floor(splatIndex / uTexSize.x);
        vec2 uv = (vec2(col, row) + 0.5) / uTexSize;

        vec4 centerRgba = texture2D(uCenterTex, uv);
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

        vec4 covA = texture2D(uCovATex, uv);
        vec4 covB = texture2D(uCovBTex, uv);

        // Unpack lossless RGBA
        float rg = centerRgba.w;
        float r = mod(rg, 256.0) / 255.0;
        float g = floor(rg / 256.0) / 255.0;
        float b = covB.z;
        float a = covB.w;

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
${SHADER_COLOR}        vQuadCoord = position.xy;

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

  initSortWorker(workerPositions, splatIdxAttr);
}

function initSortWorker(positions, splatIdxAttr) {
  const workerCode = `
    let pos = null;
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
      if (!pos) return;
      const vx = e.data.viewZ[0];
      const vy = e.data.viewZ[1];
      const vz = e.data.viewZ[2];
      const vw = e.data.viewZ[3];

      let minD = 1e9, maxD = -1e9;
      // Compute camera Z (in Three.js camera looks down -Z, so more negative Z is further away; sort back-to-front = ascending Z)
      for (let i = 0; i < count; i++) {
        const z = pos[i*3]*vx + pos[i*3+1]*vy + pos[i*3+2]*vz + vw;
        if (z < minD) minD = z;
        if (z > maxD) maxD = z;
      }
      const scale = 65535.0 / Math.max(1e-4, maxD - minD);
      counts.fill(0);
      for (let i = 0; i < count; i++) {
        const z = pos[i*3]*vx + pos[i*3+1]*vy + pos[i*3+2]*vz + vw;
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
      self.postMessage({ sorted: out }, [out.buffer]);
    };
  `;
  const blob = new Blob([workerCode], { type: "application/javascript" });
  sortWorker = new Worker(URL.createObjectURL(blob));
  sortWorker.postMessage({ positions }, [positions.buffer]);

  sortWorker.onmessage = (e) => {
    splatIdxAttr.array = e.data.sorted;
    splatIdxAttr.needsUpdate = true;
    sortBusy = false;
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
  state.cameraPreset = preset;
  document.querySelectorAll("[data-cam]").forEach((b) => {
    b.classList.toggle("active", b.dataset.cam === preset);
  });

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

  const baseMesh = new THREE.Mesh(
    new THREE.CylinderGeometry(0.13, 0.15, 0.10, 24),
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
    new THREE.BoxGeometry(0.14, 0.09, 0.18),
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
  const floor=reachableFloor(navigation,robotPosition());
  mapBackground=createGameMap(navigation,floor,state.twin.assets);
  document.getElementById("map-coverage").textContent="Outlined objects block movement";
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
    const target=route.asset_id?state.assetsById.get(route.asset_id):null;
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

  if (state.journey.phase === "moving") {
    const nextTime=Math.min(route.duration_s,state.journey.elapsed+dt*state.speed);
    try {
      advanceAlongRoute(state.twin.navigation,route,robotPosition(),state.journey.elapsed,nextTime);
      applyJourneyEvent({type:"progress",elapsed:nextTime});
    } catch(error) {
      navigationError(error);
    }
  }

  const sampled = sampleRoute(route, state.journey.elapsed);
  const nav = state.journey.phase === "blocked" ? {...sampled,...robotPosition()} : sampled;
  currentNavigation = nav;
  const {x: rx, z: rz, yaw} = nav;
  robotGroup.position.set(rx, 0, rz);
  recordPassedAssets({x:rx,z:rz});
  robotGroup.rotation.y = yaw;
  if (lidarRing) lidarRing.rotation.y = elapsedSec * 5.0;

  if (state.cameraPreset === "walk") {
    // Both camera and robot follow the validated corridor; no offset through walls.
    const behind = sampleRoute(route, Math.max(0, state.journey.elapsed - 1.2));
    const ahead = sampleRoute(route, Math.min(route.duration_s, state.journey.elapsed + 0.8));
    camera.position.set(behind.x, 1.12, behind.z);
    controls.target.set(ahead.x, 1.05, ahead.z);
    if (state.journey.phase === "arrived" || Math.hypot(ahead.x - behind.x, ahead.z - behind.z) < 0.05) {
      const target = route.asset_id ? state.assetsById.get(route.asset_id) : null;
      if (target) controls.target.fromArray(target.center);
    }
    controls.update();
  } else controls.update();

  const refreshHud = elapsedSec - hudLastUpdate >= 0.15 || hudLastUpdate < 0;
  if (refreshHud) {
    hudLastUpdate = elapsedSec;
    state.nearby = nearbyAssets(state.twin.assets, rx, rz, state.twin.navigation.grid_meta.proximity_radius_m);
    renderProximityStream(state.nearby);
  }
  const nearbyMap = new Map((state.nearby || []).map(a => [a.id, a.distance_m]));

  // Update 3D box visibility cleanly (matching IMG_4398.HEIC: focus on selected + nearby shortlist)
  for (const [objId, entry] of boxMeshes.entries()) {
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

}

function recordPassedAssets(position) {
  const summary = state.walkSummary;
  if (!summary) return;
  const from = summary.previous, dx = position.x - from.x, dz = position.z - from.z;
  const length2 = dx * dx + dz * dz;
  const radius = state.twin.navigation.grid_meta.proximity_radius_m || 1.65;
  for (const asset of state.twin.assets) {
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

  const filtered = state.twin.assets.filter((a) => {
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
  state.selectedAsset = asset;
  copilotRequestId++;
  const presentation = assetPresentation(asset);
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
  document.body.classList.toggle("inspector-open",open);
  document.getElementById("btn-inspector").setAttribute("aria-expanded",String(open));
}

function showArrivalReport(assetId) {
  const asset=state.assetsById.get(assetId);
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
  const button=document.getElementById("btn-play");
  button.disabled=view.disabled;
  button.textContent=view.button;
  document.getElementById("nav-hud-state").textContent=view.status;
  document.getElementById("nav-hud-target").textContent=view.destination;
  document.getElementById("btn-drop-obstacle").disabled = !!state.demoObstacle || !["moving","paused"].includes(journey.phase) || journey.route.total_distance_m-nav.distance < 2;
  const remaining=Math.max(0,journey.route.total_distance_m-nav.distance);
  document.getElementById("nav-hud-metrics").textContent=idle ? "Choose a map point, equipment, or room tour." : `${remaining.toFixed(1)} m remaining · ${view.remainingSeconds} s`;
  document.getElementById("sim-dist-label").textContent=idle?"":`${nav.distance.toFixed(1)} / ${journey.route.total_distance_m.toFixed(1)} m`;
  document.getElementById("navigation-message").textContent=view.error;
  document.getElementById("map-feedback").textContent=view.error;
}

function activateRoute(route, {preserveWalk=false, preserveHistory=false} = {}) {
  const result=transitionJourney(state.journey,{type:"start",route,position:robotPosition()});
  setInspectorOpen(false);
  document.getElementById("arrival-announcement").textContent="";
  state.journey=result.journey;
  if (!preserveWalk) state.walkSummary = {passed:new Set(),attention:new Set(),previous:robotPosition()};
  if (!preserveHistory) clearRouteHistory();
  recordPassedAssets(robotPosition());
  rebuildRouteGeometry();rebuildReachableOverlay();applyCameraPreset("walk");
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

function dropDemoObstacle() {
  try {
    const route=state.journey.route;
    const result=planObstacleDrop(state.twin.navigation,route,robotPosition(),state.journey.elapsed);
    clearRouteHistory();
    state.previousRoute=route;
    const points=route.waypoints.map(w=>new THREE.Vector3(w.position[0],.035,w.position[2]));
    previousRouteLine=new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),
      new THREE.LineBasicMaterial({color:0xc0c9cc,transparent:true,opacity:.28,depthWrite:false}));
    scene.add(previousRouteLine);
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

function robotPosition() {return {x:robotGroup.position.x,z:robotGroup.position.z};}

function navigationError(error) {
  applyJourneyEvent({type:"block",message:error.message});
}

function dispatchRobotToAsset(asset) {
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
  if (!attract.enabled || attract.active || document.hidden || now-attract.lastActivity < 20000) return;
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
    if(!point){navigationError(new Error("Choose a point inside the room walls."));return;}
    const objects=state.twin.assets.filter(asset=>{
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
