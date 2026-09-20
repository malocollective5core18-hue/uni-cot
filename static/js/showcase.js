(function () {
  'use strict';
  var scopes = document.querySelectorAll('.showcase-scope');
  if (!scopes.length) return;

  var reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  scopes.forEach(function (scope) {
    var cards = scope.querySelectorAll('.showcase-card');
    if (!cards.length || reduced) return;
    scope.classList.add('showcase-reveal-ready');
    cards.forEach(function (card, index) {
      card.style.setProperty('--showcase-reveal-delay', Math.min(index % 4, 3) * 90 + 'ms');
    });
    if (!('IntersectionObserver' in window)) {
      cards.forEach(function (card) { card.classList.add('showcase-card--visible'); });
      return;
    }
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('showcase-card--visible');
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.14, rootMargin: '0px 0px -8% 0px' });
    cards.forEach(function (card) { observer.observe(card); });
  });
}());
