const assert = require('node:assert/strict');

const demoStorage = require('../mysite/static/js/public-timetable-demo.js');
const key = 'ring0_public_timetable_demo_test';
let data = new Map();
globalThis.localStorage = {
  getItem: name => data.has(name) ? data.get(name) : null,
  setItem: (name, value) => data.set(name, String(value)),
  removeItem: name => data.delete(name),
};

assert.equal(demoStorage.read(key), null, 'empty storage must choose the local sample');
const edit = { schedules: [{ id: 'test-1', title: 'Edited locally' }], venues: ['Hall A'] };
assert.equal(demoStorage.write(key, edit), true, 'demo edit should be saved in this browser');
assert.deepEqual(demoStorage.read(key), edit, 'saved demo edit must survive a reload');

data.set(key, '{broken json');
assert.equal(demoStorage.read(key), null, 'corrupt storage must choose the local sample');
data.set(key, JSON.stringify({ schedules: ['invalid entry'] }));
assert.equal(demoStorage.read(key), null, 'malformed demo state must be rejected');

globalThis.localStorage = {
  getItem: () => { throw new Error('storage blocked'); },
  setItem: () => { throw new Error('storage blocked'); },
  removeItem: () => { throw new Error('storage blocked'); },
};
assert.equal(demoStorage.read(key), null, 'blocked storage must not break demo initialization');
assert.equal(demoStorage.write(key, edit), false, 'blocked storage should report unsaved state');
assert.equal(demoStorage.remove(key), false, 'blocked storage removal should fail safely');
console.log('Public demo storage checks passed.');
