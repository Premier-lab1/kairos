/**
 * three.js scene for the hero ("cluster signal"). Pure imperative module: receives the
 * lazily imported `three` namespace, a canvas and the overlay elements, returns a handle.
 * Nothing here runs on the server.
 */

import {
  CAMERA,
  CLUSTERS,
  CONFIDENCE,
  DESKTOP_QUERY,
  DUST,
  INSIDERS,
  NODES,
  PRESETS,
  RING_RADIUS,
  TOKEN,
  arcControl,
  clusterPoint,
  dustPoint,
  mulberry32,
  type Vec3,
} from "./layout";

// type-only: erased at build time, three itself is loaded lazily by the caller
import type { Line, LineBasicMaterial, Points, QuadraticBezierCurve3, ShaderMaterial, Sprite } from "three";

type ThreeNS = typeof import("three");

export type HudState = { minute: number; inside: number; confidence: number };

export type SceneOptions = {
  canvas: HTMLCanvasElement;
  /** label element per NODES entry, same order */
  labels: (HTMLElement | null)[];
  tokenLabel: HTMLElement | null;
  /** fewer points, lower DPR cap */
  small: boolean;
  onHud: (s: HudState) => void;
  /** called after the first frame was rendered */
  onReady: () => void;
  onContextLost: () => void;
};

export type SceneHandle = {
  start: () => void;
  stop: () => void;
  resize: (w: number, h: number) => void;
  /** 0..1 — how far the hero has scrolled out of view */
  setScroll: (p: number) => void;
  /** pointer parallax target, both in -1..1 */
  setPointer: (x: number, y: number) => void;
  /** accumulated drag rotation, radians */
  addDrag: (dy: number) => void;
  dispose: () => void;
};

const ACCENT = 0xff4f17;
const LOOP = 16;
const EVENTS = [1.6, 3.4, 5.2]; // insiders light up (seconds into the loop)
const RING_AT = [6.2, 7.8];
const FADE_AT = [13.6, 14.6];
const ARC_SEGMENTS = 64;
const RING_SEGMENTS = 160;

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);
const env = (t: number, a: number, b: number) => clamp01((t - a) / (b - a));
const ease = (x: number) => x * x * (3 - 2 * x);
const easeOut = (x: number) => 1 - (1 - x) * (1 - x) * (1 - x);

/** Soft round sprite generated on a canvas — no image files. */
function makeSpriteTexture(THREE: ThreeNS) {
  const size = 64;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d")!;
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.25, "rgba(255,255,255,0.7)");
  g.addColorStop(0.6, "rgba(255,255,255,0.18)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(c);
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = false;
  return tex;
}

const pointsVertex = /* glsl */ `
  attribute float aSize;
  attribute float aAlpha;
  attribute float aSeed;
  uniform float uTime;
  uniform float uScale;
  varying float vAlpha;
  void main() {
    float t = uTime * 0.22 + aSeed * 6.2831;
    vec3 p = position + vec3(sin(t * 1.1 + aSeed * 3.0), cos(t * 0.9 + aSeed * 5.0), sin(t * 0.7 + aSeed * 7.0)) * 0.045;
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = aSize * uScale * (14.0 / -mv.z);
    vAlpha = aAlpha * (0.78 + 0.22 * sin(uTime * 1.4 + aSeed * 40.0));
  }
`;
const pointsFragment = /* glsl */ `
  uniform sampler2D uMap;
  uniform vec3 uColor;
  uniform float uOpacity;
  varying float vAlpha;
  void main() {
    float a = texture2D(uMap, gl_PointCoord).a;
    gl_FragColor = vec4(uColor, a * vAlpha * uOpacity);
  }
`;

export function createHeroScene(THREE: ThreeNS, opts: SceneOptions): SceneHandle {
  const { canvas } = opts;
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: false,
    alpha: true,
    powerPreference: "high-performance",
  });
  renderer.setClearColor(0x000000, 0);
  const dprCap = opts.small ? 1.5 : 2;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, dprCap));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(CAMERA.fov, 1, 0.1, 80);
  camera.position.set(0, 0, CAMERA.z);

  const world = new THREE.Group(); // parallax / tilt
  scene.add(world);

  const sprite = makeSpriteTexture(THREE);
  const disposables: { dispose: () => void }[] = [sprite];
  const rng = mulberry32(777);

  // ---- point clouds --------------------------------------------------------
  const pointsUniforms = { uTime: { value: 0 }, uScale: { value: 1 } };
  type Cloud = { obj: Points; mat: ShaderMaterial; center: Vec3 };
  const clouds: Cloud[] = [];

  function makeCloud(color: string, n: number, gen: () => [number, number, number], opacity: number, sizeMul = 1) {
    const pos = new Float32Array(n * 3);
    const size = new Float32Array(n);
    const alpha = new Float32Array(n);
    const seed = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const p = gen();
      pos[i * 3] = p[0];
      pos[i * 3 + 1] = p[1];
      pos[i * 3 + 2] = p[2];
      // three populations: fine grain, a few bright sparks, and rare huge soft "haze" blobs
      const r = rng();
      const haze = r < 0.012;
      const big = !haze && r < 0.07;
      size[i] = (haze ? 46 + rng() * 60 : big ? 7 + rng() * 6 : 2.6 + rng() * 3.4) * sizeMul;
      alpha[i] = haze ? 0.035 + rng() * 0.035 : big ? 0.55 + rng() * 0.4 : 0.35 + rng() * 0.55;
      seed[i] = rng();
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
    geo.setAttribute("aAlpha", new THREE.BufferAttribute(alpha, 1));
    geo.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));
    const mat = new THREE.ShaderMaterial({
      uniforms: {
        uTime: pointsUniforms.uTime,
        uScale: pointsUniforms.uScale,
        uMap: { value: sprite },
        uColor: { value: new THREE.Color(color) },
        uOpacity: { value: opacity },
      },
      vertexShader: pointsVertex,
      fragmentShader: pointsFragment,
      transparent: true,
      depthWrite: false,
      depthTest: false,
      blending: THREE.AdditiveBlending,
    });
    const obj = new THREE.Points(geo, mat);
    obj.frustumCulled = false;
    disposables.push(geo, mat);
    return { obj, mat };
  }

  for (const c of CLUSTERS) {
    const n = opts.small ? c.countSmall : c.count;
    // points are generated relative to the cluster centre so the object can be moved/scaled
    const local = { ...c, center: [0, 0, 0] as Vec3 };
    const { obj, mat } = makeCloud(c.color, n, () => clusterPoint(rng, local), 1);
    obj.position.set(...c.center);
    world.add(obj);
    clouds.push({ obj, mat, center: c.center });
  }
  const dust = makeCloud(DUST.color, opts.small ? DUST.countSmall : DUST.count, () => dustPoint(rng), 0.45, 0.8);
  world.add(dust.obj);

  // ---- token ---------------------------------------------------------------
  const token = new THREE.Group();
  token.position.set(...TOKEN);
  world.add(token);

  function makeSprite(color: number, scale: number, opacity: number) {
    const mat = new THREE.SpriteMaterial({
      map: sprite,
      color,
      transparent: true,
      opacity,
      depthWrite: false,
      depthTest: false,
      blending: THREE.AdditiveBlending,
    });
    const s = new THREE.Sprite(mat);
    s.scale.setScalar(scale);
    disposables.push(mat);
    return s;
  }
  const glow = makeSprite(ACCENT, 3.2, 0.5);
  const core = makeSprite(ACCENT, 0.7, 1);
  const hot = makeSprite(0xfff1e8, 0.22, 1);
  token.add(glow, core, hot);

  // radial "hair" rays
  const RAYS = 56;
  const rayPos = new Float32Array(RAYS * 6);
  for (let i = 0; i < RAYS; i++) {
    const a = (i / RAYS) * Math.PI * 2 + rng() * 0.08;
    const r0 = 0.22 + rng() * 0.1;
    const r1 = 0.5 + Math.pow(rng(), 2.2) * 0.9;
    const z = (rng() - 0.5) * 0.4;
    rayPos.set([Math.cos(a) * r0, Math.sin(a) * r0, z, Math.cos(a) * r1, Math.sin(a) * r1, z], i * 6);
  }
  const rayGeo = new THREE.BufferGeometry();
  rayGeo.setAttribute("position", new THREE.BufferAttribute(rayPos, 3));
  const rayMat = new THREE.LineBasicMaterial({
    color: ACCENT,
    transparent: true,
    opacity: 0.4,
    depthWrite: false,
    depthTest: false,
    blending: THREE.AdditiveBlending,
  });
  const rays = new THREE.LineSegments(rayGeo, rayMat);
  token.add(rays);
  disposables.push(rayGeo, rayMat);

  // confidence ring (track + fill), from 12 o'clock clockwise
  const ringPos = new Float32Array((RING_SEGMENTS + 1) * 3);
  for (let i = 0; i <= RING_SEGMENTS; i++) {
    const a = Math.PI / 2 - (i / RING_SEGMENTS) * Math.PI * 2;
    ringPos.set([Math.cos(a) * RING_RADIUS, Math.sin(a) * RING_RADIUS, 0], i * 3);
  }
  const ringGeo = new THREE.BufferGeometry();
  ringGeo.setAttribute("position", new THREE.BufferAttribute(ringPos, 3));
  const trackMat = new THREE.LineBasicMaterial({ color: ACCENT, transparent: true, opacity: 0.12, depthWrite: false, depthTest: false });
  const ringMat = new THREE.LineBasicMaterial({
    color: ACCENT,
    transparent: true,
    opacity: 0.8,
    depthWrite: false,
    depthTest: false,
    blending: THREE.AdditiveBlending,
  });
  const track = new THREE.Line(ringGeo, trackMat);
  const ring = new THREE.Line(ringGeo, ringMat);
  ring.geometry = ringGeo.clone(); // own drawRange
  token.add(track, ring);
  disposables.push(ringGeo, ring.geometry, trackMat, ringMat);

  // ---- shiller nodes + arcs ------------------------------------------------
  type NodeVis = {
    pos: Vec3;
    dot: Sprite;
    halo: Sprite;
    arc?: Line;
    arcMat?: LineBasicMaterial;
    packet?: Sprite;
    curve?: QuadraticBezierCurve3;
    eventIndex: number; // -1 for context nodes
  };
  const nodes: NodeVis[] = NODES.map((n) => {
    const inside = n.inside;
    const dot = makeSprite(0xededeb, inside ? 0.16 : 0.11, inside ? 0.35 : 0.5);
    const halo = makeSprite(0xdfe6ee, 0.9, 0);
    dot.position.set(...n.pos);
    halo.position.set(...n.pos);
    world.add(dot, halo);
    const vis: NodeVis = { pos: n.pos, dot, halo, eventIndex: inside ? INSIDERS.findIndex((i) => i.shillerId === n.shillerId) : -1 };
    if (inside) {
      const ctrl = arcControl(n.pos, TOKEN);
      const curve = new THREE.QuadraticBezierCurve3(
        new THREE.Vector3(...n.pos),
        new THREE.Vector3(...ctrl),
        new THREE.Vector3(...TOKEN),
      );
      const pts = curve.getPoints(ARC_SEGMENTS);
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      const col = new Float32Array(pts.length * 3);
      const from = new THREE.Color(0xededeb);
      const to = new THREE.Color(ACCENT);
      const tmp = new THREE.Color();
      for (let i = 0; i < pts.length; i++) {
        tmp.copy(from).lerp(to, Math.pow(i / (pts.length - 1), 0.7));
        col.set([tmp.r, tmp.g, tmp.b], i * 3);
      }
      geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
      geo.setDrawRange(0, 0);
      const mat = new THREE.LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.75,
        depthWrite: false,
        depthTest: false,
        blending: THREE.AdditiveBlending,
      });
      const arc = new THREE.Line(geo, mat);
      arc.frustumCulled = false;
      world.add(arc);
      disposables.push(geo, mat);
      const packet = makeSprite(0xfff1e8, 0.2, 0);
      world.add(packet);
      vis.arc = arc;
      vis.arcMat = mat;
      vis.packet = packet;
      vis.curve = curve;
    }
    return vis;
  });

  // ---- framing / resize ----------------------------------------------------
  let width = 1;
  let height = 1;
  const mq = window.matchMedia(DESKTOP_QUERY);
  function applyFraming() {
    const p = mq.matches ? PRESETS.desktop : PRESETS.mobile;
    camera.aspect = width / height;
    camera.zoom = p.s;
    camera.setViewOffset(width, height, -p.dx * width, -p.dy * height, width, height);
    camera.updateProjectionMatrix();
    // aSize is "CSS px at a 900px-tall viewport, zoom 1, at the token's depth"
    pointsUniforms.uScale.value = (height / 900) * p.s * renderer.getPixelRatio();
  }
  function resize(w: number, h: number) {
    width = Math.max(1, w);
    height = Math.max(1, h);
    renderer.setSize(width, height, false);
    applyFraming();
  }

  // ---- interaction state ---------------------------------------------------
  let scroll = 0;
  let pointerX = 0;
  let pointerY = 0;
  let drag = 0;
  let rotX = 0;
  let rotY = 0;

  // ---- animation -----------------------------------------------------------
  let raf = 0;
  let running = false;
  let last = 0;
  let elapsed = 8; // start in the "rest" phase so the first frame matches the SSR still
  let ready = false;
  const hud: HudState = { minute: -1, inside: -1, confidence: -1 };
  const v = new THREE.Vector3();

  function projectToScreen(p: Vec3, out: { x: number; y: number; z: number }) {
    v.set(p[0], p[1], p[2]);
    world.localToWorld(v);
    v.project(camera);
    out.x = (v.x * 0.5 + 0.5) * width;
    out.y = (-v.y * 0.5 + 0.5) * height;
    out.z = v.z;
  }
  const scr = { x: 0, y: 0, z: 0 };
  function placeLabel(el: HTMLElement | null, p: Vec3, opacity: number, dx = 10, dy = -8) {
    if (!el) return;
    if (opacity <= 0.01) {
      if (el.style.opacity !== "0") el.style.opacity = "0";
      return;
    }
    projectToScreen(p, scr);
    el.style.transform = `translate3d(${(scr.x + dx).toFixed(1)}px, ${(scr.y + dy).toFixed(1)}px, 0)`;
    el.style.opacity = opacity.toFixed(2);
  }

  function frame(now: number) {
    if (!running) return;
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - last) / 1000 || 0);
    last = now;
    elapsed += dt;
    const t = elapsed % LOOP;

    // --- timeline envelopes
    const birth = easeOut(env(t, 0, 1.2));
    const fade = ease(env(t, FADE_AT[0], FADE_AT[1]));
    const alive = birth * (1 - fade);
    const pulse = 0.5 + 0.5 * Math.sin(elapsed * 2.1);

    // token
    const coreS = 0.7 * (0.15 + 0.85 * alive) * (1 + 0.05 * pulse);
    core.scale.setScalar(coreS);
    hot.scale.setScalar(0.22 * alive);
    glow.scale.setScalar(3.2 * (0.4 + 0.6 * alive) * (1 + 0.08 * pulse));
    glow.material.opacity = 0.5 * alive * (0.8 + 0.2 * pulse);
    core.material.opacity = alive;
    rays.scale.setScalar(0.25 + 0.75 * alive);
    rays.rotation.z += dt * 0.06;
    rayMat.opacity = 0.42 * alive;

    // confidence ring
    const ringP = ease(env(t, RING_AT[0], RING_AT[1])) * (1 - fade);
    ring.geometry.setDrawRange(0, Math.round(ringP * CONFIDENCE * RING_SEGMENTS) + (ringP > 0 ? 1 : 0));
    trackMat.opacity = 0.12 * alive;

    // insiders
    let insideCount = 0;
    let minute = 0;
    for (const n of nodes) {
      if (n.eventIndex < 0) {
        n.dot.material.opacity = 0.5;
        continue;
      }
      const e = EVENTS[n.eventIndex];
      const light = ease(env(t, e, e + 0.45)) * (1 - fade);
      const draw = easeOut(env(t, e + 0.2, e + 1.1));
      if (light > 0.5) insideCount++;
      n.dot.material.opacity = 0.35 + 0.65 * light;
      n.dot.scale.setScalar(0.16 + 0.1 * light);
      n.halo.material.opacity = 0.28 * light * (0.7 + 0.3 * pulse);
      n.halo.scale.setScalar(0.9 + 0.3 * light);
      const arc = n.arc!;
      arc.geometry.setDrawRange(0, draw > 0 ? Math.floor(draw * ARC_SEGMENTS) + 1 : 0);
      n.arcMat!.opacity = 0.75 * (1 - fade);
      // travelling packet: head of the arc while drawing, then periodic pulses along it
      const packet = n.packet!;
      if (draw > 0 && draw < 1) {
        n.curve!.getPoint(draw, packet.position);
        packet.material.opacity = 0.9;
      } else if (draw >= 1 && fade < 1) {
        const u = ((t - (e + 1.1)) / 2.6 + n.eventIndex * 0.37) % 1;
        n.curve!.getPoint(u, packet.position);
        packet.material.opacity = 0.75 * (1 - u) * (1 - fade);
      } else {
        packet.material.opacity = 0;
      }
    }
    // minute counter follows the events (replay feel)
    {
      const mins = INSIDERS.map((i) => i.minute ?? 0);
      if (t < EVENTS[0]) minute = Math.round((t / EVENTS[0]) * mins[0]);
      else if (t < EVENTS[1]) minute = Math.round(mins[0] + ((t - EVENTS[0]) / (EVENTS[1] - EVENTS[0])) * (mins[1] - mins[0]));
      else if (t < EVENTS[2]) minute = Math.round(mins[1] + ((t - EVENTS[1]) / (EVENTS[2] - EVENTS[1])) * (mins[2] - mins[1]));
      else minute = mins[2];
    }
    const confidence = Math.round(ringP * CONFIDENCE * 100);
    if (hud.minute !== minute || hud.inside !== insideCount || hud.confidence !== confidence) {
      hud.minute = minute;
      hud.inside = insideCount;
      hud.confidence = confidence;
      opts.onHud(hud);
    }

    // --- scroll: dolly in, tilt, clusters tighten toward the token
    const sp = ease(scroll);
    camera.position.z = CAMERA.z - 4.5 * sp;
    for (const c of clouds) {
      const k = 1 - 0.5 * sp;
      c.obj.position.set(c.center[0] * k, c.center[1] * k, c.center[2] * k);
      c.obj.scale.setScalar(1 - 0.35 * sp);
    }
    // --- parallax (lerped) + drag + idle sway
    const targetY = pointerX * 0.16 + drag + Math.sin(elapsed * 0.17) * 0.04;
    const targetX = -pointerY * 0.1 + 0.32 * sp + Math.sin(elapsed * 0.13) * 0.03;
    rotY += (targetY - rotY) * Math.min(1, dt * 4);
    rotX += (targetX - rotX) * Math.min(1, dt * 4);
    world.rotation.set(rotX, rotY, 0);
    world.updateMatrixWorld();

    pointsUniforms.uTime.value = elapsed;
    renderer.render(scene, camera);

    // --- HTML labels (projected after the render so matrices are fresh)
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      let o: number;
      if (n.eventIndex < 0) o = 0.42 * birth;
      else {
        const e = EVENTS[n.eventIndex];
        o = env(t, e + 0.9, e + 1.4) * (1 - fade);
      }
      placeLabel(opts.labels[i], n.pos, o);
    }
    placeLabel(opts.tokenLabel, TOKEN, alive, 26, -34);

    if (!ready) {
      ready = true;
      opts.onReady();
    }
  }

  let disposed = false;
  function start() {
    if (running || disposed) return;
    running = true;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  }
  function stop() {
    running = false;
    cancelAnimationFrame(raf);
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    stop();
    canvas.removeEventListener("webglcontextlost", onLost);
    for (const d of disposables) d.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
  }

  // A lost context is terminal for this page view: tear everything down so no loop can
  // restart on an invisible canvas; the caller falls back to the SSR still.
  const onLost = (e: Event) => {
    e.preventDefault();
    dispose();
    opts.onContextLost();
  };
  canvas.addEventListener("webglcontextlost", onLost);

  return {
    start,
    stop,
    resize,
    setScroll: (p) => (scroll = clamp01(p)),
    setPointer: (x, y) => {
      pointerX = x;
      pointerY = y;
    },
    addDrag: (d) => (drag = Math.max(-0.6, Math.min(0.6, drag + d))),
    dispose,
  };
}
