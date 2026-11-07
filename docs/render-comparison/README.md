# Renderer comparison

The existing PLY renderer now uses a bounded quality policy. At the checked 1224 × 772 CSS viewport, High detail changed the drawing buffer from 2448 × 1544 to 3060 × 1930. Balanced remains available for lower GPU load. This is a sampling increase, not a measurement of recovered scene detail or a performance improvement.

The screenshots use the same initial room camera, closed inspector, and equipment frames turned off. The browser captures the displayed page at the same screenshot dimensions, rather than exporting the full drawing buffer. The visible definition difference is modest. Soft surfaces that are already in the PLY remain soft.

- [Before](before.jpg) shows the original renderer.
- [Higher-resolution comparison](after-resolution.jpg) shows the first resolution change.
- [Final renderer](after.jpg) shows the retained settings.
- [Filter experiment](filter-experiment.jpg) shows an opacity-compensation trial. It showed no clear definition benefit in this view and was rejected.

The retained change also uses the actual framebuffer for shader projection and keeps the splat-size limit consistent across quality settings. Depth sorting, blending, colors, camera presets, Gaussian filtering, geometry, and collision data remain unchanged.

The filtering trial followed the determinant compensation used in the [GaussianSplats3D shader](https://github.com/mkkellogg/GaussianSplats3D/blob/main/src/splatmesh/SplatMaterial3D.js). That technique is not enabled in the final renderer.

Run `node tests/test_render_quality.mjs` for profile, pixel-budget, hardware-bound, fractional-size, and hidden-view checks. The navigation and journey suites cover the existing movement behavior. Browser checks passed for High and Balanced selection, viewport resizing, opening the inspector, and equipment arrival. At a temporary 1100 × 720 browser viewport, the canvas resized to 862 × 668 CSS pixels with a 1724 × 1336 drawing buffer. The original viewport was restored afterward. CV-003 arrival still opened the correct report and evidence at High detail. The browser console reported no errors.

Pstack Model the Domain put sampling and bounds in one quality policy. Prove It Works required same-view screenshots and checks against the real canvas. Separate Before Serializing Shared State kept renderer edits under one owner and left detection artifacts unchanged.
