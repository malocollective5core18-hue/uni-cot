import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function harness() {
  const timers = [];
  const listeners = {};
  let hidden = false;
  let requests = 0;
  const math = Object.create(Math);
  math.random = () => 0;
  const context = {
    console,
    Math: math,
    setTimeout: (fn) => (timers.push(fn), timers.length - 1),
    clearTimeout: (id) => { timers[id] = null; },
    document: {
      get hidden() { return hidden; },
      addEventListener: (name, fn) => { listeners[name] = fn; }
    },
    window: { scrollX: 0, scrollY: 0, scrollTo() {} },
    fetch: () => {
      requests++;
      return Promise.resolve({ status: 304, ok: false, headers: { get: () => null } });
    }
  };
  context.window.TenantSync = { subscribe: () => {} };
  vm.runInNewContext(fs.readFileSync(new URL('./tenant-live.js', import.meta.url), 'utf8'), context);
  return { context, timers, listeners, setHidden: (value) => { hidden = value; }, requests: () => requests };
}

test('register schedules one jittered timer and refreshNow is single-flight', async () => {
  const h = harness();
  h.context.window.TenantLive.register({ resource: 'members', url: '/members', onData() {} });
  assert.equal(h.timers.filter(Boolean).length, 1);
  h.context.window.TenantLive.refreshNow('members');
  h.context.window.TenantLive.refreshNow('members');
  assert.equal(h.requests(), 1);
});

test('hidden pages pause and visibility resumes a stale resource', () => {
  const h = harness();
  h.setHidden(true);
  h.context.window.TenantLive.register({ resource: 'groups', url: '/groups', onData() {} });
  assert.equal(h.timers.filter(Boolean).length, 0);
  h.setHidden(false);
  h.listeners.visibilitychange();
  assert.equal(h.requests(), 1);
});
