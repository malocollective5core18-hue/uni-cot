(function () {
  'use strict';
  var match = window.location.pathname.match(/^\/t\/[^/]+\/[^/]+\/([^/]+)/);
  var key = match ? match[1] : 'public';
  var name = 'tenant-sync:' + key;
  var channel = null;
  var listeners = [];
  if ('BroadcastChannel' in window) channel = new BroadcastChannel(name);

  function deliver(message) {
    if (!message || typeof message.resource !== 'string' || !message.ts) return;
    listeners.slice().forEach(function (listener) { listener(message); });
  }
  if (channel) channel.onmessage = function (event) { deliver(event.data); };
  window.addEventListener('storage', function (event) {
    if (event.key !== name || !event.newValue) return;
    try { deliver(JSON.parse(event.newValue)); } catch (error) { /* ignore */ }
  });

  window.TenantSync = {
    publish: function (resource) {
      var message = { resource: resource, ts: Date.now() };
      if (channel) channel.postMessage(message);
      try { localStorage.setItem(name, JSON.stringify(message)); } catch (error) { /* ignore */ }
    },
    subscribe: function (listener) {
      if (typeof listener !== 'function') return function () {};
      listeners.push(listener);
      return function () { listeners = listeners.filter(function (item) { return item !== listener; }); };
    }
  };
}());
