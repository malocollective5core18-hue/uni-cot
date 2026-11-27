/* Browser-only storage for the public timetable demo. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.UniCotDemoStorage = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function validState(value) {
    return Boolean(
      value && typeof value === 'object' && !Array.isArray(value) &&
      Array.isArray(value.schedules) &&
      value.schedules.every(item => item && typeof item === 'object' && !Array.isArray(item)) &&
      (value.venues === undefined || Array.isArray(value.venues))
    );
  }

  function read(key) {
    try {
      const raw = globalThis.localStorage.getItem(key);
      if (!raw) return null;
      const value = JSON.parse(raw);
      return validState(value) ? value : null;
    } catch (error) {
      return null;
    }
  }

  function write(key, value) {
    try {
      globalThis.localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (error) {
      return false;
    }
  }

  function remove(key) {
    try {
      globalThis.localStorage.removeItem(key);
      return true;
    } catch (error) {
      return false;
    }
  }

  return { read, write, remove };
});
