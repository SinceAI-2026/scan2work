# Record the Field Atlas demo

The video is scored on five criteria, so time is spent in proportion to them: extraction 30%, navigation and proximity 25%, AI diagnosis 20%, backend 15%, clarity 10%. Target 4 minutes 30 seconds. Keep the final video below five minutes.

## Prepare the recording

1. Rebuild with the final labels, then render the AR overlay clip (about 2 minutes).

   ```sh
   bash scripts/check.sh
   .venv/bin/python -m scripts.render_overlay      # writes web/data/overlay_walk.mp4
   ```

2. Start the server and open `http://127.0.0.1:8765/`. After a rebuild, restart the server before recording.

   ```sh
   .venv/bin/python server.py 8765
   ```

3. Rehearse **Inspect faulty device**. Arrival must open CV-003 (`ASSET-003`) with its diagnosis, evidence and inspection steps.
4. Keep the scorecard output (`python -m tests.scorecard`) and `docs/technical-notes.md` open for the numbers. Read every **[refresh]** value from the final run, not from this file.
5. Close terminals and settings that contain credentials.

## Follow the sequence

| Time | Criterion | Action | Narration |
| --- | --- | --- | --- |
| 0:00–0:20 | Clarity | Room overview, then the map. | "A service engineer walks into an equipment room they have never seen. Field Atlas turns the room's 3D scan into a digital twin: every object framed and named, a safe route to the faulty device, and an AI diagnosis grounded in Valmet's data." |
| 0:20–1:10 | Extraction | Turn on all frames, then run the **exploded view** so objects separate. Hover a pump, a valve, a pipe cylinder, a movable cart and the washing machine. | "From 1.7 million Gaussians we extract **[refresh] objects**. Each object is labelled by AI from its real photo in the walkthrough video: on hand-labelled ground truth that was 85% correct from renders, **[refresh]** from photos. Pipes are fitted as true cylinders. Movable objects such as carts and ladders are flagged so a robot knows they can move." |
| 1:10–1:45 | Extraction / innovation | Play 25 s of `overlay_walk.mp4`, ending as the faulty valve comes into view. | "Because the twin is aligned to the real camera, we can draw it over the real video. This is Valmet's tablet use case: the engineer's camera view, with every device, its tag and its status. The red frame is the faulty valve." |
| 1:45–2:50 | Navigation | Select **Inspect faulty device**. Show the route bending around equipment. Pause once on the nearby-assets panel (statuses). Resume to the stand-off point. | "The robot plans the shortest collision-free route on a 5 cm occupancy grid built from the scan, with its own radius plus a safety margin. As it walks, every asset within 2.5 m is framed with its status from the Excel pack. We checked the map against the real walkthrough: only 1.8% of the path a person walked is marked as obstacle." |
| 2:50–3:55 | AI diagnosis | Arrival opens the CV-003 panel. Show observations with evidence IDs, the root cause, severity, lock-out/tag-out (LOTO) steps, then **Draft work order**. | "On arrival the AI diagnoses CV-003. Every statement cites telemetry, alarms or service cases from the pack, retrieved with EmbeddingGemma. In a blind test, with the answer hidden and telemetry only, it named the right root cause first for 9 of 13 faulty assets, against 2 for a baseline. Steps include LOTO before any hands-on check." |
| 3:55–4:30 | Backend | One slide: pipeline diagram, then the scorecard output. | "Everything is one automated Python pipeline: ingest, extraction, AI labelling, detection lifted to 3D, pipe fitting, Excel binding, navigation and diagnostics, checked by a scorecard against the brief on every run." |

Keep the remaining 30 seconds as margin.

## Keep the claims precise

- Quote object counts and accuracy from the final artifact and test output, not from this file.
- The Excel mapping is procedural, as the brief allows. The fault is synthetic; the filmed valve is not actually faulty.
- Collision checks are grid-based movement checks, not a physics simulation.
- Label accuracy is measured on a 39-object sample: say "on our hand-labelled sample".
