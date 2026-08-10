/** Pre-hydration guard, injected as an inline <script> by the gallery page.
 *
 * Why this exists: the shims in `lib/dev/gallery-env` install when the gallery
 * ROUTE's client chunk evaluates — but Next hydrates the LAYOUT first, and the
 * layout's ThemeProvider fires a real `/api/workspace/custom-themes` request on
 * mount. Measured: that call escaped to 127.0.0.1:8000 (CORS error in the
 * console, and on the founder's box a live backend would have answered it with
 * real data). A harness that leaks real requests isn't frozen.
 *
 * So this runs during HTML parse, before ANY React code:
 *   - `/api/*` fetches are captured and PARKED until the fixture handler
 *     registers, then replayed through it — nothing reaches the network.
 *   - the backend market/notification WebSocket is replaced by a socket that
 *     never opens (Next's own HMR socket is untouched).
 */
export const GALLERY_FETCH_GUARD = `(function(){
  if (window.__hmtGalleryGuard) return;
  window.__hmtGalleryGuard = true;

  var realFetch = window.fetch.bind(window);
  var parked = [];

  window.__hmtGalleryReal = realFetch;
  window.__hmtGalleryFlush = function () {
    var queue = parked;
    parked = [];
    queue.forEach(function (item) {
      item.resolve(window.__hmtGalleryFixture(item.input, item.init));
    });
  };

  window.fetch = function (input, init) {
    var path = "";
    try {
      var raw = typeof input === "string"
        ? input
        : (input instanceof URL ? input.href : input.url);
      path = new URL(raw, location.origin).pathname;
    } catch (e) { path = ""; }

    // The backend surface is /api/* plus the bare /health probe.
    if (path.indexOf("/api/") !== 0 && path !== "/health") {
      return realFetch(input, init);
    }
    if (window.__hmtGalleryFixture) return window.__hmtGalleryFixture(input, init);
    return new Promise(function (resolve) {
      parked.push({ input: input, init: init, resolve: resolve });
    });
  };

  var RealWebSocket = window.WebSocket;
  window.WebSocket = new Proxy(RealWebSocket, {
    construct: function (target, args) {
      if (String(args[0]).indexOf("/ws/") >= 0) {
        var dead = new EventTarget();
        dead.url = String(args[0]);
        dead.readyState = 0;
        dead.bufferedAmount = 0;
        dead.binaryType = "blob";
        dead.send = function () {};
        dead.close = function () {};
        return dead;
      }
      return new target(args[0], args[1]);
    }
  });
})();`;
