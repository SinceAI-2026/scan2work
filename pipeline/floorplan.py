"""Stage 6c - Semantic floor plan: an architectural-style plan of the room, generated from the scan.

Valmet's request (7 Nov): point clouds are hard to read from afar, so give service workers a clean layout of the
room in advance (in the spirit of scan-to-BIM "plan generated, not drawn"). Everything comes from our own outputs:
  * walls: the outline of the observed floor (floor splats + object footprints, closed and filled), simplified with
    Douglas-Peucker and snapped to the room axes, drawn as a thick wall line;
  * floor-standing objects: their true oriented footprints, filled by category; Excel-bound devices outlined in
    their condition colour, the faulty target in red with a halo;
  * overhead items (pipes and anything above head height): dashed, as ceiling items are drawn in architecture;
    fitted pipe cylinders at their real diameter;
  * walkable floor (robot-safe area) tinted, the inspection route to the target, start and stand-off;
  * labels: Excel tags and names of large items, placed without overlaps; legend, scale bar and title block.
Writes web/data/floorplan_semantic.{svg,pdf,png}.
"""
from __future__ import annotations

import math
import time

import numpy as np
from scipy import ndimage

from . import config as C

RES = 0.05
CAT = {  # internal class -> (category, fill colour)
    "control_valve": ("Valve", "#60a5fa"), "circulation_pump": ("Pump / motor", "#2dd4bf"),
    "electric_motor": ("Pump / motor", "#2dd4bf"), "heat_exchanger": ("Pump / motor", "#2dd4bf"),
    "process_unit": ("Pump / motor", "#2dd4bf"), "pressure_sensor": ("Instrument", "#c084fc"),
    "flow_marker": ("Instrument", "#c084fc"), "expansion_vessel": ("Tank / vessel", "#fdba74"),
    "electrical_cabinet": ("Cabinet / panel", "#94a3b8"), "pipe_fitting": ("Pipe / fitting", "#d1d5db"),
    "vertical_pipe": ("Pipe / fitting", "#d1d5db"), "horizontal_pipe": ("Pipe / fitting", "#d1d5db"),
    "overhead_pipe": ("Pipe / fitting", "#d1d5db"), "structural_column": ("Structure", "#6b7280"),
    "appliance": ("Facility item", "#e7dcc0"), "sink": ("Facility item", "#e7dcc0"), "shelf_rack": ("Facility item", "#e7dcc0"),
}
MOVABLE_FILL = "#f3f0ff"
STATUS = {"target": "#dc2626", "degraded": "#f97316", "attention_required": "#f97316", "watch": "#eab308",
          "healthy": "#16a34a"}
OVERHEAD_Y = 1.70        # an object whose bottom is above this is drawn as a ceiling item (dashed)


def _footprint(a):
    cx, _, cz = a["center"]; sx, _, sz = a["size"]; c, s = math.cos(a.get("yaw", 0)), math.sin(a.get("yaw", 0))
    pts = [(-sx / 2, -sz / 2), (sx / 2, -sz / 2), (sx / 2, sz / 2), (-sx / 2, sz / 2)]
    return np.array([[cx + lx * c - lz * s, cz + lx * s + lz * c] for lx, lz in pts])


def _dp(pts, tol):
    """Douglas-Peucker simplification of a closed polyline."""
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    ab = b - a
    n = np.linalg.norm(ab)
    r = pts - a
    d = np.abs(ab[0] * r[:, 1] - ab[1] * r[:, 0]) / n if n > 1e-9 else np.linalg.norm(r, axis=1)
    k = int(np.argmax(d))
    if d[k] > tol:
        return np.vstack([_dp(pts[:k + 1], tol)[:-1], _dp(pts[k:], tol)])
    return np.array([a, b])


def _orthogonalise(poly, max_dev_deg=20):
    """Snap nearly axis-parallel wall segments to the room axes (scan-to-BIM walls are straight)."""
    p = poly.copy()
    n = len(p)
    for _ in range(2):
        for i in range(n):
            j = (i + 1) % n
            d = p[j] - p[i]
            ang = math.degrees(math.atan2(abs(d[1]), abs(d[0])))
            if ang < max_dev_deg:          # horizontal-ish: same z
                z = (p[i, 1] + p[j, 1]) / 2; p[i, 1] = p[j, 1] = z
            elif ang > 90 - max_dev_deg:   # vertical-ish: same x
                x = (p[i, 0] + p[j, 0]) / 2; p[i, 0] = p[j, 0] = x
    return p


def room_outline(splat, assets):
    x = splat["xyz"]
    m = splat["roi"] & (splat["opacity"] > 0.1) & (x[:, 1] > -0.06) & (x[:, 1] < 0.10)
    x0, z0 = -4.75, -3.55
    nx, nz = int(8.9 / RES) + 1, int(7.3 / RES) + 1
    i = np.clip(((x[m, 0] - x0) / RES).astype(int), 0, nx - 1); j = np.clip(((x[m, 2] - z0) / RES).astype(int), 0, nz - 1)
    cnt = np.zeros((nx, nz)); np.add.at(cnt, (i, j), 1)
    occ = cnt >= 3
    gx = x0 + (np.arange(nx)[:, None] + 0.5) * RES; gz = z0 + (np.arange(nz)[None, :] + 0.5) * RES
    for a in assets:                         # everything below head height sits inside the walls
        if a["center"][1] - a["size"][1] / 2 > OVERHEAD_Y or a["class"] == "overhead_pipe":
            continue
        c, s = math.cos(a.get("yaw", 0)), math.sin(a.get("yaw", 0)); dx, dz = gx - a["center"][0], gz - a["center"][2]
        occ |= (np.abs(dx * c + dz * s) <= a["size"][0] / 2) & (np.abs(-dx * s + dz * c) <= a["size"][2] / 2)
    room = ndimage.binary_closing(occ, np.ones((7, 7)), iterations=2)
    room = ndimage.binary_opening(ndimage.binary_fill_holes(room), np.ones((5, 5)))
    room = ndimage.binary_dilation(room, iterations=2)       # wall line just outside the outermost objects
    lab, n = ndimage.label(room)
    if n == 0:
        return None
    room = lab == (np.argmax(np.bincount(lab.ravel())[1:]) + 1)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure()
    cs = plt.contour(gx[:, 0], gz[0, :], room.T.astype(float), [0.5])
    plt.close(fig)
    segs = cs.allsegs[0]
    if not segs:
        return None
    ring = max(segs, key=len)
    poly = _dp(np.asarray(ring), 0.12)[:-1]
    return _orthogonalise(poly)


def _place_labels(items, ax_bounds, obstacles):
    """Greedy, non-overlapping label placement around each anchor (priority order). items: (anchor, w, h, ...)"""
    placed, out = [], []
    x_lo, x_hi, z_lo, z_hi = ax_bounds
    for it in items:
        (ax_, az_), w, h = it["anchor"], it["w"], it["h"]
        best = None
        for r in (0.25, 0.45, 0.7, 1.0, 1.4):
            for ang in range(0, 360, 30):
                cx = ax_ + r * math.cos(math.radians(ang)); cz = az_ + r * math.sin(math.radians(ang))
                box = (cx - w / 2, cz - h / 2, cx + w / 2, cz + h / 2)
                if box[0] < x_lo or box[2] > x_hi or box[1] < z_lo or box[3] > z_hi:
                    continue
                if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in placed):
                    continue
                pen = sum(1 for b in obstacles if not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]))
                cost = pen * 0.6 + r
                if best is None or cost < best[0]:
                    best = (cost, box, (cx, cz))
            if best and best[0] < r + 0.6:
                break
        if best:
            placed.append(best[1]); out.append((it, best[2]))
    return out


def build(splat, assets, nav, out_dir=C.OUT_DIR):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon, Circle, Rectangle, FancyBboxPatch
    from matplotlib.lines import Line2D

    poly = room_outline(splat, assets)
    g = nav["grid_meta"]
    xs = poly[:, 0] if poly is not None else np.array([-4.65, 4.05])
    zs = poly[:, 1] if poly is not None else np.array([-3.45, 3.65])
    x_lo, x_hi, z_lo, z_hi = xs.min() - 0.6, xs.max() + 0.6, zs.min() - 0.6, zs.max() + 0.6
    W, H = x_hi - x_lo, z_hi - z_lo
    fig = plt.figure(figsize=(16, 16 * (H + 1.6) / (W + 4.2)))
    ax = fig.add_axes([0.02, 0.10, 0.70, 0.86])
    ax.set_xlim(x_lo, x_hi); ax.set_ylim(z_hi, z_lo); ax.set_aspect("equal"); ax.axis("off")

    # 1 m grid
    for gxv in np.arange(math.ceil(x_lo), x_hi, 1.0):
        ax.plot([gxv, gxv], [z_lo, z_hi], color="#f1f5f9", lw=0.6, zorder=0)
    for gzv in np.arange(math.ceil(z_lo), z_hi, 1.0):
        ax.plot([x_lo, x_hi], [gzv, gzv], color="#f1f5f9", lw=0.6, zorder=0)
    # walkable floor (robot-safe area)
    walk = np.array(nav["walkable_grid"], bool)
    ax.imshow(np.where(walk.T, 1.0, np.nan), extent=(g["x_min"], g["x_min"] + walk.shape[0] * g["res"],
              g["z_min"] + walk.shape[1] * g["res"], g["z_min"]), cmap=matplotlib.colors.ListedColormap(["#ecfdf5"]),
              interpolation="nearest", zorder=1, vmin=0, vmax=1)
    # walls
    if poly is not None:
        ax.add_patch(Polygon(poly, closed=True, fill=False, ec="#111827", lw=7, joinstyle="miter", zorder=8))

    floor_items, over_items = [], []
    for a in assets:
        bottom = a["center"][1] - a["size"][1] / 2
        (over_items if bottom > OVERHEAD_Y or a["class"] == "overhead_pipe" else floor_items).append(a)
    # overhead first (dashed, under everything)
    for a in over_items:
        cyl = a.get("cylinder")
        if cyl and cyl.get("axis") != "y":
            ax.plot([cyl["start"][0], cyl["end"][0]], [cyl["start"][2], cyl["end"][2]], color="#94a3b8", lw=1.2,
                    ls=(0, (4, 3)), zorder=2)
        else:
            ax.add_patch(Polygon(_footprint(a), closed=True, fill=False, ec="#cbd5e1", lw=0.8, ls=(0, (4, 3)), zorder=2))
    obstacles = []
    order = sorted(floor_items, key=lambda a: -np.prod(a["size"][::2]))           # big first, small on top
    for a in order:
        cat, fill = CAT.get(a["class"], ("Other", "#e2e8f0"))
        cyl = a.get("cylinder")
        ec, lw, ls = "#475569", 0.6, "-"
        if a.get("movable"):
            fill, ls, ec, lw = MOVABLE_FILL, (0, (3, 2)), "#a78bfa", 0.5
        if a.get("mapped"):
            ec = STATUS["target"] if a.get("is_target") else STATUS.get(a.get("condition_state"), "#16a34a")
            lw = 3.0 if a.get("is_target") else 1.8
        if cyl and cyl.get("axis") == "y":
            ax.add_patch(Circle((cyl["start"][0], cyl["start"][2]), max(cyl["radius"], 0.03), fc=fill, ec=ec, lw=lw, zorder=4,
                                gid=a["id"]))
        elif cyl:
            ax.plot([cyl["start"][0], cyl["end"][0]], [cyl["start"][2], cyl["end"][2]], color="#9ca3af",
                    lw=max(1.5, cyl["radius"] * 2 * 72 * 16 / (W + 4.2) / 1.0 * 0.9), solid_capstyle="butt", zorder=3)
        else:
            fp = _footprint(a)
            ax.add_patch(Polygon(fp, closed=True, fc=fill, ec=ec, lw=lw, ls=ls, zorder=4 if not a.get("mapped") else 6,
                                 gid=a["id"]))          # SVG element id = object id: the viewer can make it clickable
            obstacles.append((fp[:, 0].min(), fp[:, 1].min(), fp[:, 0].max(), fp[:, 1].max()))
        if a.get("is_target"):
            ax.add_patch(Circle((a["center"][0], a["center"][2]), 0.42, fill=False, ec=STATUS["target"], lw=1.5,
                                ls=(0, (2, 2)), zorder=7))
    # route
    route = nav.get("primary_route", {}).get("waypoints") or []
    if route:
        rx = [w["position"][0] for w in route]; rz = [w["position"][2] for w in route]
        ax.plot(rx, rz, color="#2563eb", lw=2.4, zorder=9, solid_capstyle="round")
        ax.plot(rx[:1], rz[:1], "o", ms=11, mfc="#2563eb", mec="white", mew=2, zorder=10)
        ax.plot(rx[-1:], rz[-1:], "s", ms=10, mfc="white", mec="#2563eb", mew=2.5, zorder=10)
        ax.annotate("START", (rx[0], rz[0]), xytext=(8, -12), textcoords="offset points", fontsize=8.5,
                    fontweight="bold", color="#1d4ed8", zorder=11)

    # labels: Excel tags first (target first), then names of large items
    fsz = 7.2
    cw = (W + 4.2) / 16 / 72 * fsz * 0.62          # metres per character at this font size
    items = []
    for a in sorted([a for a in floor_items if a.get("mapped")], key=lambda a: (not a.get("is_target"), a["tag"])):
        txt = f"{a['tag']}" + (" ⚠ FAULT" if a.get("is_target") else "")
        items.append({"a": a, "txt": txt, "anchor": (a["center"][0], a["center"][2]), "w": cw * len(txt) + 0.08, "h": 0.2})
    big = [a for a in floor_items if not a.get("mapped") and a["class"] in ("expansion_vessel", "electrical_cabinet",
           "appliance", "heat_exchanger", "shelf_rack", "sink") and max(a["size"][0], a["size"][2]) > 0.35]
    for a in sorted(big, key=lambda a: -np.prod(a["size"])):
        name = (a.get("name") or a.get("label") or a["class"]).split("(")[0].strip()
        name = (name[:1].upper() + name[1:])[:30]
        items.append({"a": a, "txt": name, "anchor": (a["center"][0], a["center"][2]), "w": cw * len(name) + 0.08, "h": 0.2})
    for it, (lx, lz) in _place_labels(items, (x_lo, x_hi, z_lo, z_hi), obstacles):
        a = it["a"]
        col = (STATUS["target"] if a.get("is_target") else STATUS.get(a.get("condition_state"), "#334155")) if a.get("mapped") else "#334155"
        ax.plot([it["anchor"][0], lx], [it["anchor"][1], lz], color=col, lw=0.6, zorder=11)
        ax.text(lx, lz, it["txt"], ha="center", va="center", fontsize=fsz, color=col if a.get("mapped") else "#1f2937",
                fontweight="bold" if a.get("mapped") else "normal", zorder=12,
                bbox=dict(boxstyle="round,pad=0.18", fc="white", ec=col, lw=0.6 if not a.get("is_target") else 1.4))

    # scale bar
    sx0, sz0 = x_lo + 0.3, z_hi - 0.25
    for k in range(2):
        ax.add_patch(Rectangle((sx0 + k, sz0 - 0.06), 1, 0.06, fc="#111827" if k == 0 else "white", ec="#111827", lw=0.8, zorder=12))
    for k, t in enumerate(("0", "1", "2 m")):
        ax.text(sx0 + k, sz0 - 0.12, t, ha="center", va="bottom", fontsize=7.5, zorder=12)

    # legend + title block
    lg = fig.add_axes([0.74, 0.10, 0.25, 0.86]); lg.axis("off"); lg.set_xlim(0, 1); lg.set_ylim(1, 0)
    lg.text(0, 0.02, "Equipment Room", fontsize=17, fontweight="bold", va="top")
    lg.text(0, 0.07, "Semantic floor plan", fontsize=12, va="top", color="#334155")
    n_dev = sum(1 for a in assets if a.get("mapped"))
    lg.text(0, 0.115, f"Generated automatically from the Gaussian-splat scan\n{len(assets)} objects · {n_dev} Excel devices · "
            f"{time.strftime('%d %b %Y')}", fontsize=8, va="top", color="#64748b")
    y = 0.20
    lg.text(0, y, "Objects (true footprints)", fontsize=9, fontweight="bold", va="top"); y += 0.035
    seen = []
    for cls, (cat, fill) in CAT.items():
        if cat in seen:
            continue
        seen.append(cat)
        lg.add_patch(Rectangle((0, y - 0.004), 0.06, 0.022, fc=fill, ec="#475569", lw=0.6))
        lg.text(0.09, y + 0.007, cat, fontsize=8, va="center"); y += 0.032
    lg.add_patch(Rectangle((0, y - 0.004), 0.06, 0.022, fc=MOVABLE_FILL, ec="#475569", lw=0.6, ls=(0, (3, 2))))
    lg.text(0.09, y + 0.007, "Movable (cart, chair, crate, ladder…)", fontsize=8, va="center"); y += 0.032
    lg.plot([0, 0.06], [y + 0.007] * 2, color="#94a3b8", lw=1.2, ls=(0, (4, 3)))
    lg.text(0.09, y + 0.007, "Overhead pipe / ceiling item", fontsize=8, va="center"); y += 0.05
    lg.text(0, y, "Excel device status (outline)", fontsize=9, fontweight="bold", va="top"); y += 0.035
    for name, key in (("Faulty target", "target"), ("Degraded / attention", "degraded"), ("Watch", "watch"), ("Healthy", "healthy")):
        lg.add_patch(Rectangle((0, y - 0.004), 0.06, 0.022, fc="white", ec=STATUS[key], lw=2.2))
        lg.text(0.09, y + 0.007, name, fontsize=8, va="center"); y += 0.032
    y += 0.02
    lg.text(0, y, "Navigation", fontsize=9, fontweight="bold", va="top"); y += 0.035
    lg.add_patch(Rectangle((0, y - 0.004), 0.06, 0.022, fc="#ecfdf5", ec="#a7f3d0", lw=0.6))
    lg.text(0.09, y + 0.007, "Robot-safe floor (0.15 m radius + margin)", fontsize=8, va="center"); y += 0.032
    lg.plot([0, 0.06], [y + 0.007] * 2, color="#2563eb", lw=2.4)
    route_m = nav.get("primary_route", {}).get("total_distance_m")
    lg.text(0.09, y + 0.007, f"Route to target ({route_m} m)" if route_m else "Route to target", fontsize=8, va="center"); y += 0.032
    lg.plot([0, 0.06], [y + 0.007] * 2, color="#111827", lw=4)
    lg.text(0.09, y + 0.007, "Wall (outline of the scanned floor)", fontsize=8, va="center"); y += 0.06
    lg.text(0, y, "Coordinates: scan axes, metres, grid 1 m.\nNot a survey drawing: footprints come from the\n"
            "AI-extracted 3D boxes; the fault is synthetic.", fontsize=7, va="top", color="#94a3b8")

    out = {}
    for ext in ("svg", "pdf", "png"):
        p = out_dir / f"floorplan_semantic.{ext}"
        fig.savefig(p, dpi=170 if ext == "png" else None, facecolor="white")
        out[ext] = f"./data/floorplan_semantic.{ext}"
    plt.close(fig)
    print(f"   [floorplan] semantic floor plan written (svg/pdf/png), {len(floor_items)} footprints, "
          f"{len(over_items)} overhead items")
    return out
