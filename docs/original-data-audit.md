# Original Wael archive versus the expanded import

The supplied `field-atlas-data.zip` resolves the previous missing-artifact limitation.
**No Gaussian visible in the original renderer was removed from the current renderer.
However, replacing the original asset list did drop its object identities, grouping,
labels, photo associations and equipment-attached geometry.** The two segmentations
are different inventories, not an original model plus extra objects.

This audit is read-only. It did not replace either dataset, merge objects, change
navigation, run AI models or read credentials. Before/after hashes verify that the
live manifest, render buffers, source-row map and compact membership file were unchanged.

## Inputs and exact correspondence

- Archive SHA256: `e7dea068e4292a562e5d726ec236395d2b896311795cf27c8992561feba44a9e`.
- Original manifest SHA256: `ed4234be4023faf6daedc4120ee83441506bfd808936479b0ffde3a277df18dc`.
- Full original source PLY: 1,676,481 rows; SHA256 `83d8c7d99316c08c9719cfc2c5a6b9d1903b89847780e2458adc662c79f32955`.
- Original renderer: 1,046,910 rows. Every row matches the original source processing
  in order, with **zero position, covariance, RGB-byte or alpha-byte error**. The audit
  reproduces Wael's original scale clamp and export filter. No nearest-neighbour
  approximation is used.
- Current renderer: 1,131,951 rows, containing **all 1,046,910 original visible rows**
  plus 85,041 additional rows. The full source PLY also remains available.

## Actual inventories

| Layer | Original archive | Current import after context recovery |
| --- | ---: | ---: |
| Objects / regions | 504 objects | 1,933 unreviewed regions |
| Category vocabulary entries | 24 classes | 17 categories, including unclassified/floor suggestions |
| Rows assigned by actual GSID | 598,437 | 711,568 |
| Canonical Excel bindings | 30 | 30, procedurally reassigned |
| Objects with native video photo evidence | 471 | 415, with 1,191 native references |
| Regions with rendered proposal evidence only | — | 1,514 |
| Attached pipe-cylinder geometries | 83 | 0 |
| Separate unreviewed pipe-fit context | 0 | 120 |
| Nonzero canonical box rotations | 62 | 0; 427 rotated auxiliary envelopes |
| Objects with detection-view lists | 280 | 0 |
| Objects with `merged_from` / `merged_fragments` | 31 / 16 | 0 / 0 |
| Wall guides | 4 configured guides | Same 4 guides, explicitly marked assumptions |
| Unmapped objects carrying telemetry profiles | 321 | 0 |

The archive manifest reports **852,475** object splats, but its supplied GSID actually
assigns **598,437**. The latter is authoritative for this comparison. This discrepancy
is retained in the machine-readable report instead of silently trusting the manifest.
Likewise, detector `n_gaussians` counts differ from exported GSID ownership: original
`pipeline.run.fill_object_membership` fills unassigned source rows inside object boxes.
Neither those filled boxes nor the current mask proposals are independent ground truth.

The current 1,929 photo references comprise 415 native-video associations and 1,514
rendered proposals. They must not be described as 1,929 equivalent source photographs
or as better evidence than the original 471 native photo associations.

## Membership, grouping and semantic associations

Across the full original source-row index:

- 332,826 rows belong to an object in both inventories.
- **265,611 rows previously assigned to an original object are now unassigned context**,
  while still rendered. Their original semantic association was lost, not their scan data.
- 378,742 rows are assigned in the current inventory but were not assigned originally.
- 499 of 504 original objects overlap at least one current region. Five have no current
  owned-row overlap: OBJ-136 (pump), OBJ-149 (pipe riser), OBJ-303 (elbow fitting),
  OBJ-352 and OBJ-420 (valves). Their visible source rows remain in scene context.
- 274 original groups overlap multiple current regions, each containing at least 10%
  and 10 source rows of the original group. Conversely, 403 current regions have multiple
  original contributors, each contributing at least 10% and 10 rows of the current region.
- Only 111 original objects have at least half their original GSID rows inside one
  current region. 29 have less than 10% assigned anywhere in the current inventory.

These are **source-row grouping statistics**, not accuracy scores or proof that either
set represents the correct physical objects. Box-filled ownership can inflate overlap;
partial masks can fragment a complete assembly. Labels and parent/part relationships
need preserved provenance and review rather than automatic transfer by largest overlap.

Of the 30 equal Excel asset IDs, 27 original/current object pairs share no owned source
rows. Only P-002, CV-003 and P-005 overlap. This is procedural mapping drift, not proof
that the synthetic workbook mapping was incorrect. Equal tags do not establish physical
correspondence, and copying the original 321 auxiliary telemetry profiles would undo
the current explicit distinction between mapped equipment and unknown regions.

### Concrete example: CV-003

The native photos show the same orange actuator area, but the object extent changed:

| Measure | Original OBJ-206 | Current linards-BOX-0173 |
| --- | --- | --- |
| Centre in source units | [2.124, 1.061, -1.597] | [2.15394, 1.08105, -1.49491] |
| Reported detector / membership count | 346 detector Gaussians | 171 exact member Gaussians |
| Actual GSID-assigned rows | 1,231 | 171 |
| Shared rows between these two objects | 170 | 170 |

Thus 170/171 current rows lie inside the original membership, but they cover only
13.81% of that larger original group. In total, 485 original CV-003 rows are distributed
across 11 current regions, with 746 remaining as unassigned scene context. This explains
why the current isolated item can look less complete even though the room scan was retained.

## Why the original showed more green floor

Stored navigation data alone gives a misleading comparison:

| Walkable mask | Cells | Area in square source units |
| --- | ---: | ---: |
| Original stored navigation | 4,824 | 12.0600 |
| **Original browser after walkthrough-passage expansion** | **7,225** | **18.0625** |
| Current imported browser | 5,321 | 13.3025 |

The original frontend calls `withWalkthroughPassages` with 334 tracked camera poses,
opens 2,401 additional cells and changes its robot assumptions from radius 0.15 and
margin 0.08 to radius 0.10 and margin 0.01. Its stored `coverage` record is not recomputed,
so it still says 12.06 while the browser displays 18.06. The imported viewer deliberately
does not apply this expansion. The old displayed mask contains 2,184 cells absent from
the current mask; the current mask contains 280 absent from the old displayed mask.

The audit reproduces the actual frontend function. Its relevant upstream files match
commit `ab777d3`; the only `manual-walk` change skips imported `source_region_id` assets,
which are absent from the original archive, so original behavior is unchanged.
The pose file SHA256 is `cbe9d9f7ec93c3dd9783f10784ec9f777796f7ee1713172de565e0fffc2497e6`.

This explains the visual floor difference without claiming that the more permissive
mask is physically correct or safer. Floor illustration, observed low-surface samples,
walkable-cell policy and physical scale are separate concerns. Neither dataset supplies
an independently verified scale or complete floor/obstacle ground truth.

## Safest integration approach

1. Keep the supplied original viewer/data as a reproducible baseline with its 504 object
   identities, photo links, merges, attached cylinders and original procedural mappings.
2. Add the 1,933 exact regions as a separate extraction/proposal layer, linked to original
   objects through this audited source-row correspondence. Preserve both ownership maps.
3. Review split/merged relationships and unassigned portions explicitly. Start with the
   CV-003 example and the five original objects with zero current assigned overlap.
4. Attach original labels and photographs to their original semantic objects; show suggested
   parent/part links for new regions rather than silently overwriting their identities.
5. Offer original walkthrough floor and conservative imported navigation as explicitly
   named policies. Do not silently open the latter's unknown cells or change robot clearance.
6. Keep synthetic metadata bindings and unmapped status explicit. Use a mapping migration
   table if choosing the original 30 physical demo locations as the future canonical mapping.

No automatic merge or replacement was performed during this audit.

## Reproduce

Run from the fork's repository directory, with outputs outside both datasets:

```sh
VALMET_OFFLINE=1 .venv/bin/python -m pipeline.audit_original_data \
  --original-data /Users/perttu/Downloads/SinceAI-2026/Wael-original-data-2026-11-07/web/data \
  --current-data web/data --output /tmp/wael-original-data-audit.json

node scripts/audit_runtime_floor.mjs \
  --original-data /Users/perttu/Downloads/SinceAI-2026/Wael-original-data-2026-11-07/web/data \
  --current-data web/data --poses web/data/walk_poses.json \
  --output /tmp/wael-runtime-floor-audit.json

VALMET_OFFLINE=1 .venv/bin/python -m unittest discover -s tests -p test_original_data_audit.py -v
```

The four audit tests pass, covering protected output paths (including symlinks), GSID
context handling, invalid/duplicate ownership and split/context overlap accounting.
The JSON reports contain every original object's overlap list, original photos/cylinders,
all 30 binding comparisons and before/after hashes of live inputs. Output-path guards
reject either input tree before reading data, preventing a mistyped output from replacing
an input manifest or binary.
