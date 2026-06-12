// Desktop / localhost-only terminal drawer (GCP Cloud Shell style) — the client
// half of app/terminal.py. Loaded by base.html only for local clients; the markup
// (#term-drawer) is gated server-side the same way, and the websocket itself
// re-verifies the peer, so this file being world-readable under /static is fine.
(function () {
  var drawer = document.getElementById('term-drawer');
  var toggle = document.getElementById('term-toggle');
  if (!drawer || !toggle) return;
  // vendored xterm assets (no remote CDN in the RCE surface) — base.html stamps the
  // URLs on the drawer with static_url(), so they get cache-busted like every asset
  var CSS = drawer.dataset.xtermCss, XJS = drawer.dataset.xtermJs, FJS = drawer.dataset.fitJs;
  var OPEN_KEY = 'al-term-open', SID_KEY = 'al-term-sid', H_KEY = 'al-term-h', MIN_KEY = 'al-term-min';
  var statusEl = document.getElementById('term-status');
  var dotEl = document.getElementById('term-dot');
  var screenEl = document.getElementById('term');
  var enc = new TextEncoder();
  var loaded = null, term = null, fit = null, ws = null, ro = null, refit = null;
  var sentRows = 0, sentCols = 0;        // last size sent on the CURRENT socket

  function fail(m) { statusEl.hidden = false; statusEl.textContent = m; }
  function sendResize() {                // skip no-op resizes (each one costs the server an ioctl + the TUI a redraw)
    if (!term || !ws || ws.readyState !== 1) return;
    if (term.rows === sentRows && term.cols === sentCols) return;
    sentRows = term.rows; sentCols = term.cols;
    ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }));
  }
  function focusSoon() {                 // refit + focus once the drawer has its final size
    setTimeout(function () { if (refit) refit(); if (term) term.focus(); }, 60);
  }

  function loadAssets() {
    if (loaded) return loaded;
    loaded = new Promise(function (res, rej) {
      if (window.Terminal && window.FitAddon) return res();
      var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = CSS; document.head.appendChild(l);
      var s = document.createElement('script'); s.src = XJS;
      s.onload = function () {
        var f = document.createElement('script'); f.src = FJS;
        f.onload = res; f.onerror = rej; document.head.appendChild(f);
      };
      s.onerror = rej; document.head.appendChild(s);
    });
    return loaded;
  }

  function ready(cb) {                 // ensure xterm + the Terminal instance exist, then run cb
    loadAssets().then(function () {
      if (!term) {
        term = new Terminal({
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
          fontSize: 13, cursorBlink: true, scrollback: 5000,
          theme: { background: '#16181d', foreground: '#e6e6e6', cursor: '#e6e6e6' }
        });
        fit = new FitAddon.FitAddon(); term.loadAddon(fit);
        term.open(screenEl); try { fit.fit(); } catch (e) {}
        refit = function () { try { fit.fit(); } catch (e) {} sendResize(); };
        term.onData(function (d) { if (ws && ws.readyState === 1) ws.send(enc.encode(d)); });
        ro = new ResizeObserver(function () { if (refit) refit(); }); ro.observe(screenEl);
      }
      cb();
    }).catch(function () { fail('Failed to load xterm.js (local asset missing).'); });
  }

  function connect() {                 // (re)attach the websocket to our persistent session
    if (ws && (ws.readyState === 0 || ws.readyState === 1)) return;
    var proto = location.protocol === 'https:' ? 'wss' : 'ws';
    var sid = localStorage.getItem(SID_KEY);
    ws = new WebSocket(proto + '://' + location.host + '/terminal/ws' + (sid ? ('?sid=' + encodeURIComponent(sid)) : ''));
    ws.binaryType = 'arraybuffer';
    sentRows = sentCols = 0;           // fresh socket (maybe a fresh PTY): always send the first resize
    ws.onopen = function () { if (dotEl) dotEl.classList.add('on'); sendResize(); if (term) term.focus(); };
    ws.onmessage = function (e) {
      if (typeof e.data === 'string') {                                    // control frame (JSON)
        try { var m = JSON.parse(e.data); if (m && m.type === 'session' && m.sid) localStorage.setItem(SID_KEY, m.sid); } catch (_) {}
        return;
      }
      if (term) term.write(new Uint8Array(e.data));                        // pty output
    };
    ws.onclose = function () { if (dotEl) dotEl.classList.remove('on'); };
    ws.onerror = function () { fail('WebSocket error — the terminal is localhost-only.'); };
  }

  function syncInset() {               // reserve page space so content can scroll above the drawer
    if (drawer.hidden) {
      document.body.classList.remove('term-open');
      document.body.style.removeProperty('--term-h');
    } else {
      document.body.classList.add('term-open');
      document.body.style.setProperty('--term-h', drawer.offsetHeight + 'px');
    }
  }
  function open() {                    // show + (re)connect; the shell persists across pages
    drawer.hidden = false; drawer.setAttribute('aria-hidden', 'false');
    var min = localStorage.getItem(MIN_KEY) === '1';
    drawer.classList.toggle('minimized', min);   // restore the prior collapsed/expanded state
    toggle.classList.add('active');
    localStorage.setItem(OPEN_KEY, '1'); statusEl.hidden = true;
    syncInset();
    ready(function () { connect(); if (!min) focusSoon(); });
  }
  function hide() {                    // collapse the panel; the shell keeps running, reattaches later
    drawer.hidden = true; drawer.setAttribute('aria-hidden', 'true');
    toggle.classList.remove('active'); localStorage.setItem(OPEN_KEY, '0');
    syncInset();
  }
  function kill() {                    // ✕ — end the session: stop the shell, forget it, then hide
    try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'kill' })); } catch (e) {}
    localStorage.removeItem(SID_KEY);
    if (ws) { try { ws.close(); } catch (e) {} }
    ws = null;
    if (dotEl) dotEl.classList.remove('on');
    hide();
  }

  toggle.addEventListener('click', function () { drawer.hidden ? open() : hide(); });
  var killBtn = document.getElementById('term-close'); if (killBtn) killBtn.addEventListener('click', kill);
  var minBtn = document.getElementById('term-min');
  if (minBtn) minBtn.addEventListener('click', function () {
    var min = drawer.classList.toggle('minimized');
    localStorage.setItem(MIN_KEY, min ? '1' : '0');   // remember collapsed state across pages
    syncInset();
    if (!min) focusSoon();
  });
  var titleEl = document.querySelector('.term-title');
  if (titleEl && minBtn) titleEl.addEventListener('dblclick', function () { minBtn.click(); });

  var H = localStorage.getItem(H_KEY); if (H) drawer.style.height = H;

  // drag-to-resize from the top edge; listeners live only for the drag's duration.
  // xterm refit comes free via the ResizeObserver on the screen as the height changes.
  var handle = document.getElementById('term-resize');
  if (handle) {
    var onDrag = function (e) {
      var h = Math.max(120, Math.min(window.innerHeight - 80, window.innerHeight - e.clientY));
      drawer.style.height = h + 'px';
      document.body.style.setProperty('--term-h', h + 'px');   // keep the content lift in sync while dragging
    };
    var endDrag = function () {
      window.removeEventListener('mousemove', onDrag);
      window.removeEventListener('mouseup', endDrag);
      document.body.style.userSelect = '';
      if (drawer.style.height) localStorage.setItem(H_KEY, drawer.style.height);
    };
    handle.addEventListener('mousedown', function (e) {
      e.preventDefault(); document.body.style.userSelect = 'none';
      window.addEventListener('mousemove', onDrag);
      window.addEventListener('mouseup', endDrag);
    });
  }

  // Ctrl+` toggles show/hide, like GCP Cloud Shell / VS Code
  window.addEventListener('keydown', function (e) {
    if (e.ctrlKey && !e.altKey && !e.metaKey && e.key === '`') { e.preventDefault(); drawer.hidden ? open() : hide(); }
  });
  // Keep the reserved space correct when the viewport changes (42vh drawer, window resize).
  window.addEventListener('resize', function () { if (!drawer.hidden) syncInset(); });

  // Reattach automatically on every page if the drawer was left open (survives navigation).
  if (localStorage.getItem(OPEN_KEY) === '1') open();
})();
