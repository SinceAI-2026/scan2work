// In-room Take apart, pure rules (web/parts-data.mjs): node tests/test_parts_data.mjs
import assert from 'node:assert/strict';
import {unitFor, shiftFor, unitDevices, faultParts, floorDistance, spreadLabels, toRaw} from '../web/parts-data.mjs';

const part = (node, serves, fault_codes = [], extra = {}) => ({node, name: node, serves, fault_codes, dir: [0, -1, 0], dist: 0.1, ...extra});
const entry = {file: 'U.glb', ids: ['B013', 'B013m'], fault_notes: {MISALIGNMENT: 'coupling'}, parts: [
  part('motor', 'B013m', ['OVERHEATING']), part('lantern', 'B013', ['MISALIGNMENT', 'SEAL_DEGRADATION']),
  part('housing', 'B013', ['FOULING']), part('base', 'B013', ['FOULING'], {context: true})]};
const doc = {models: {'U.glb': entry},
  cards: {B013: {tag: 'P-005', center: [-3.7, 0.8, -1.2]}, B013m: {tag: 'M-006', center: [-3.8, 0.4, -1.2]}},
  devices: {'P-005': {model: 'U.glb', card: 'B013', how: 'tag'}, 'P-001': {model: 'U.glb', card: 'B013', how: 'standin'}}};
const assets = {
  'P-005': {tag: 'P-005', issue_code: 'FOULING', center: [1, 1, 2], aabb_min: [0.9, 0.9, 1.9], aabb_max: [1.1, 1.1, 2.1]},
  'M-006': {tag: 'M-006', issue_code: 'OVERHEATING'},
  'P-001': {tag: 'P-001', issue_code: 'MISALIGNMENT', center: [3, 1.2, 2]},
  'CV-003': {tag: 'CV-003', issue_code: 'POSITION_DEVIATION'},
};
const byTag = t => assets[t] ?? null;

assert.equal(unitFor(doc, assets['CV-003']), null, 'devices without a unit get no Take apart');
const p5 = unitFor(doc, assets['P-005']);
assert.deepEqual(faultParts(entry, unitDevices(doc, p5, assets['P-005'], byTag)).map(f => `${f.tag}:${f.node}:${f.own}`),
  ['P-005:housing:true', 'M-006:motor:false'], 'a tag match shows its own fault and the unit mate\'s, never on a context piece');
const p1 = unitFor(doc, assets['P-001']);
assert.deepEqual(faultParts(entry, unitDevices(doc, p1, assets['P-001'], byTag)).map(f => `${f.tag}:${f.node}:${f.note}`),
  ['P-001:lantern:coupling'], 'a stand-in carries only its own issue (no P-005 fouling, no M-006 overheating)');

// the unit's centre lands on our box centre: raw = (x, 1.185 - y, -z)
const unitCenter = [-3.7, 0.6, -1.2], s = shiftFor(assets['P-005'], unitCenter), raw = toRaw(assets['P-005'].center);
assert.deepEqual(unitCenter.map((v, i) => +(v + s[i]).toFixed(6)), raw.map(v => +v.toFixed(6)));
assert.deepEqual(raw.map(v => +v.toFixed(6)), [1, 0.185, -2]);

assert.equal(floorDistance(assets['P-005'], 1, 2), 0);
assert.ok(Math.abs(floorDistance(assets['P-005'], 1.6, 2) - 0.5) < 1e-9);
assert.deepEqual(spreadLabels([100, 105, 300], 48, 80, 500), [100, 148, 300]);
console.log('parts data: ok');
