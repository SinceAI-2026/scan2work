// In-room Take apart, pure rules (web/parts-data.mjs): node tests/test_parts_data.mjs
import assert from 'node:assert/strict';
import {unitFor, unitDevices, faultParts, deviceIn, floorDistance, spreadLabels, toRaw} from '../web/parts-data.mjs';

// inline pump 2 (B019p+A069_r2): raw PLY boxes from scripts/import_take_apart.py
const part = (node, box, fault_codes = [], extra = {}) => ({node, name: node, box, fault_codes, dir: [0, -1, 0], dist: 0.1, ...extra});
const entry = {file: 'parts/U.glb', box: [[-3.435, 0.207, -1.419], [-3.087, 0.801, -0.771]], fault_notes: {CURRENT_IMBALANCE: 'supply'}, parts: [
  part('motor', [[-3.435, 0.277, -1.395], [-3.143, 0.527, -1.060]], ['OVERHEATING', 'CURRENT_IMBALANCE']),
  part('lantern', [[-3.322, 0.527, -1.293], [-3.191, 0.605, -1.121]], ['SEAL_DEGRADATION', 'MISALIGNMENT']),
  part('housing', [[-3.402, 0.599, -1.419], [-3.087, 0.801, -0.914]], ['FOULING', 'CAVITATION_RISK']),
  part('pipe_b', [[-3.318, 0.584, -0.925], [-3.129, 0.797, -0.771]], [], {context: true})]};
const doc = {models: {'U.glb': entry}};
// this viewer's objects (levelled: y up, floor 0): the motor, the casing below it, a pump elsewhere
const m005 = {id: 'OBJ-204', tag: 'M-005', issue_code: 'CURRENT_IMBALANCE', center: [-3.26, 0.76, 1.2], aabb_min: [-3.38, 0.52, 1.08], aabb_max: [-3.14, 1.0, 1.33]};
const casing = {id: 'OBJ-034', tag: '', issue_code: 'NO_PACK_RECORD', center: [-3.22, 0.49, 0.92]};
const p004 = {id: 'OBJ-234', tag: 'P-004', issue_code: 'SEAL_DEGRADATION', center: [-3.53, 1.26, -3.2]};
const assets = [m005, casing, p004];

assert.deepEqual(toRaw([1, 1, 2]).map(v => +v.toFixed(6)), [1, 0.185, -2]);
assert.equal(unitFor(doc, m005)?.name, 'U.glb', 'the motor stands inside the unit');
assert.equal(unitFor(doc, casing)?.name, 'U.glb', 'so does the casing below it');
assert.equal(unitFor(doc, p004), null, 'an object elsewhere gets no unit (position, not tag)');
const devs = unitDevices(unitFor(doc, m005), m005, assets);
assert.deepEqual(devs.map(d => `${d.id}:${d.own}`), ['OBJ-204:true', 'OBJ-034:false']);
assert.deepEqual(faultParts(entry, devs).map(f => `${f.tag}:${f.code}->${f.node}:${f.own}:${f.note}`),
  ['M-005:CURRENT_IMBALANCE->motor:true:supply'], 'its own Excel issue on the part it stands in; no record, no fault');
assert.equal(deviceIn(entry.parts[0], devs)?.tag, 'M-005');
assert.equal(deviceIn(entry.parts[2], devs)?.id, 'OBJ-034');

assert.equal(floorDistance(m005, -3.26, 1.2), 0);
assert.ok(Math.abs(floorDistance(m005, -3.26, 1.83) - 0.5) < 1e-9);
assert.deepEqual(spreadLabels([100, 105, 300], 48, 80, 500), [100, 148, 300]);
console.log('parts data: ok');
