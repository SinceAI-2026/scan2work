# Scan2Work presentation outline

Seven slides, one per scoring criterion plus an opening and a close. Pair this outline with the [recording sequence](demo-sequence.md). Read every **[refresh]** value from the final run.

## Slide 1. From a raw scan to a field-service twin

**Main point.** An engineer can know the room before entering it: every object framed and named, a safe route, and a grounded diagnosis.

**Visual.** One AR overlay frame (real video, coloured 3D frames, the CV-003 fault banner).

**Speaker note.** This is Valmet's own business case from the briefing: remote support and a faster first-time fix, with the twin registered over the real camera view.

## Slide 2. Extraction: as many objects as possible, correctly named (30%)

**Main point.** **[refresh] objects** are framed. AI labels come from real photos, and accuracy is measured, not assumed.

**Visual.** Exploded view screenshot; beside it, the accuracy ladder: colour rules 10% → Decisions API 31% / 56% → Gemini on renders 85% → Gemini on real photos **[refresh]**.

**Show.** The stages: geometric clusters, real photo per object, AI labelling, multi-view detection lifted to 3D (merges fragments into whole objects), pipe cylinders, movable objects.

## Slide 3. Real video ↔ scan alignment

**Main point.** Aligning the walkthrough video to the scan gives every object a real photo and lets us draw the twin over reality.

**Visual.** The real frame / render / 50-50 blend triptych, plus one real-photo crop with its 3D box.

**Speaker note.** This also caught our own mistake: the demo target chosen from blurry renders was a cardboard box on video. We moved it to a verified motorised control valve.

## Slide 4. Navigation and proximity (25%)

**Main point.** The robot takes the shortest collision-free route on an occupancy grid derived from the scan, and frames every nearby asset as it passes.

**Visual.** Top-down route map with the robot footprint, plus the nearby-assets panel.

**Show.** 5 cm grid, 0.15 m robot radius plus 0.08 m margin, A* with collision-checked shortcuts, 2.5 m proximity radius, 0 equipment boxes crossed, and 1.8% of the real human path on obstacle cells.

## Slide 5. Mapping and grounded AI diagnosis (20%)

**Main point.** Every status and number comes from the Excel pack, and the diagnosis cites its evidence.

**Visual.** The CV-003 panel: observations with evidence IDs, root cause, severity, LOTO, work-order draft.

**Show.** 30 workbook assets bound to scene objects (procedural, as the brief allows); retrieval with EmbeddingGemma; triage with the OpenAI Decisions API. Blind test: top-1 9/13 from telemetry alone vs. 2/13 baseline.

## Slide 6. Architecture (15%)

**Main point.** One automated, modular Python pipeline, verified on every run.

**Visual.** Pipeline diagram: ingest → perception → walk alignment / photos → AI labels → detection lifting → pipes → Excel binding → navigation → diagnostics → `digital_twin.json` → web viewer and server.

**Show.** Caching (re-runs cost no API calls), fallbacks for every AI stage, and the scorecard checks against the brief.

## Slide 7. Limits and next steps

**Main point.** The pipeline is a working end-to-end demonstrator; the next steps are about scale and live data.

**Show.** Larger hand-labelled test set; live telemetry instead of the synthetic pack; the twin as the world model for an on-robot policy; a tablet AR client using the same alignment.

**Credits.** Linards (video alignment, pipe fit), Rasse (briefing, real-walk validation idea), scan2service (sharp-frame selection).
