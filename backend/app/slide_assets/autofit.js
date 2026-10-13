/* Shrink slide content to fit the fixed 1280x720 canvas.
 *
 * The kit has no overflow handling — .slide{overflow:hidden} silently clips
 * long titles / bullet lists. This steps the --fit CSS var (see the fit-layer
 * overrides in slide_html._page) down from 1.0 to a 0.70 floor until every
 * content box fits, then signals window.__autofit_done for the renderer.
 * Injected via add_script_tag by the headless renderer (inline <script> from
 * set_content does not execute); the inline copy covers direct browser opens.
 */
(function () {
  function run() {
    var slide = document.querySelector('.slide');
    if (!slide) {
      window.__autofit_done = true;
      return;
    }
    var SELECTORS = [
      '.slide-pad',
      '.t-split .copy',
      '.t-map .copy',
      '.t-title .content',
      '.t-section .content',
      '.t-image .caption',
      '.t-closing .content',
    ];
    var boxes = [];
    SELECTORS.forEach(function (sel) {
      slide.querySelectorAll(sel).forEach(function (el) {
        boxes.push(el);
      });
    });
    var slideRect = slide.getBoundingClientRect();
    function fits() {
      for (var i = 0; i < boxes.length; i++) {
        var el = boxes[i];
        // Inner overflow (scrollable content taller than the box)...
        if (el.scrollHeight > el.clientHeight + 2) return false;
        // ...and boxes pushed outside the slide (bottom-anchored / flex-centred
        // content overflows the canvas without ever overflowing itself).
        var r = el.getBoundingClientRect();
        if (r.top < slideRect.top - 4 || r.bottom > slideRect.bottom + 4) return false;
      }
      return true;
    }
    var fit = 1.0;
    while (!fits() && fit > 0.701) {
      fit = Math.round((fit - 0.05) * 100) / 100;
      slide.style.setProperty('--fit', String(fit));
    }
    window.__autofit_done = true;
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }
})();
