// Desktop / localhost-only terminal drawer (GCP Cloud Shell style) — the client
// half of app/terminal.py. Loaded by base.html only for local clients; the markup
// (#term-drawer) is gated server-side the same way, and the websocket itself
// re-verifies the peer, so this file being world-readable under /static is fine.
(function () {
  var drawer = document.getElementById('term-drawer');
  var toggle = document.getElementById('term-toggle');
  if (!drawer || !toggle) return;

  var CSS = drawer.dataset.xtermCss, XJS = drawer.dataset.xtermJs, FJS = drawer.dataset.fitJs;
  var WGLJS = drawer.dataset.webglJs, WLJS = drawer.dataset.webLinksJs;
  var U11JS = drawer.dataset.unicode11Js, SJS = drawer.dataset.searchJs, CJS = drawer.dataset.clipboardJs;
  var OPEN_KEY = 'al-term-open';
  var LEGACY_SID_KEY = 'al-term-sid';
  var TABS_KEY = 'al-term-tabs';
  var ACTIVE_KEY = 'al-term-active';
  var H_KEY = 'al-term-h';
  var W_KEY = 'al-term-w';
  var MIN_KEY = 'al-term-min';
  var COPY_SELECT_KEY = 'al-term-copyselect';
  var MAX_TABS = 8;

  var statusEl = document.getElementById('term-status');
  var dotEl = document.getElementById('term-dot');
  var screenHost = document.getElementById('term-screens');
  var tabsEl = document.getElementById('term-tabs');
  var newBtn = document.getElementById('term-new');
  var findEl = document.getElementById('term-find');
  var findInput = document.getElementById('term-find-input');
  var findPrevBtn = document.getElementById('term-find-prev');
  var findNextBtn = document.getElementById('term-find-next');
  var findCloseBtn = document.getElementById('term-find-close');
  var enc = new TextEncoder();
  var loaded = null;
  var tabs = [];
  // Two pointers: activeId is the effective in-memory active tab; storedActiveId
  // is the durable one — the only value persistTabs() ever writes. The off-Learn
  // lesson-tab fallback changes activeId alone, so incidental persists (title
  // change, sid arrival) can never leak the transient choice into storage.
  var activeId = null;
  var storedActiveId = null;
  var idSeq = 0;

  function onLearn() {
    return document.body.dataset.rail === 'learn';
  }

  function setActive(id) {
    activeId = id;
    storedActiveId = id;
  }

  function fail(m) {
    if (!statusEl) return;
    statusEl.hidden = false;
    statusEl.textContent = m;
  }

  function clearFail() {
    if (statusEl) statusEl.hidden = true;
  }

  function newId() {
    idSeq += 1;
    return 't' + Date.now().toString(36) + '-' + idSeq.toString(36);
  }

  function cleanTitle(s, fallback) {
    s = String(s || '').replace(/[\x00-\x1f\x7f]/g, '').trim();
    return (s || fallback || 'Terminal').slice(0, 48);
  }

  function readStoredTabs() {
    var raw = null;
    try { raw = JSON.parse(localStorage.getItem(TABS_KEY) || 'null'); } catch (_) {}
    if (!Array.isArray(raw) || raw.length === 0) {
      var legacy = localStorage.getItem(LEGACY_SID_KEY);
      if (legacy) raw = [{ id: newId(), sid: legacy, title: 'Terminal 1' }];
    }
    tabs = (Array.isArray(raw) ? raw : []).slice(0, MAX_TABS).map(function (t, i) {
      return {
        id: cleanTitle(t.id, newId()),
        sid: t.sid ? String(t.sid) : null,
        lesson: t.lesson ? String(t.lesson).slice(0, 80) : null,
        title: cleanTitle(t.title, 'Terminal ' + (i + 1)),
        term: null, fit: null, search: null, clipboard: null, webgl: null, ws: null, screen: null, ro: null,
        sentRows: 0, sentCols: 0
      };
    });
    storedActiveId = localStorage.getItem(ACTIVE_KEY);
    if (!tabs.some(function (t) { return t.id === storedActiveId; })) {
      storedActiveId = tabs[0] ? tabs[0].id : null;
    }
    activeId = storedActiveId;
    if (tabs.length) persistTabs();
    localStorage.removeItem(LEGACY_SID_KEY);
    // A lesson tab must not be auto-active outside Learn: fall back to the
    // first plain tab (creating one in memory if every stored tab is a lesson
    // tab). Transient by construction — storedActiveId still points at the
    // lesson tab, so returning to Learn restores it as active.
    var act = activeTab();
    if (act && act.lesson && !onLearn()) {
      var plain = tabs.find(function (t) { return !t.lesson; });
      if (!plain && tabs.length < MAX_TABS) {
        plain = {
          id: newId(), sid: null, lesson: null, title: 'Terminal ' + (tabs.length + 1),
          term: null, fit: null, search: null, clipboard: null, webgl: null, ws: null, screen: null, ro: null,
          sentRows: 0, sentCols: 0
        };
        tabs.push(plain);
      }
      if (plain) activeId = plain.id;
    }
  }

  function persistTabs() {
    localStorage.setItem(TABS_KEY, JSON.stringify(tabs.map(function (t) {
      return { id: t.id, sid: t.sid, lesson: t.lesson || null, title: t.title };
    })));
    if (storedActiveId) localStorage.setItem(ACTIVE_KEY, storedActiveId);
    else localStorage.removeItem(ACTIVE_KEY);
  }

  function activeTab() {
    return tabs.find(function (t) { return t.id === activeId; }) || tabs[0] || null;
  }

  function ensureDefaultTab() {
    if (tabs.length) return;
    tabs.push({
      id: newId(), sid: null, lesson: null, title: 'Terminal 1',
      term: null, fit: null, search: null, clipboard: null, webgl: null, ws: null, screen: null, ro: null,
      sentRows: 0, sentCols: 0
    });
    setActive(tabs[0].id);
    persistTabs();
    renderTabs();
  }

  function renderTabs() {
    if (!tabsEl) return;
    tabsEl.textContent = '';
    tabs.forEach(function (tab, i) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'term-tab' + (tab.id === activeId ? ' active' : '');
      btn.setAttribute('role', 'tab');
      btn.setAttribute('aria-selected', tab.id === activeId ? 'true' : 'false');
      btn.title = tab.title;
      btn.dataset.tabId = tab.id;
      var label = document.createElement('span');
      label.className = 'term-tab-label';
      label.textContent = tab.title || ('Terminal ' + (i + 1));
      btn.appendChild(label);
      btn.addEventListener('click', function () { switchTab(tab.id); });
      tabsEl.appendChild(btn);
    });
    if (newBtn) newBtn.disabled = tabs.length >= MAX_TABS;
    updateActiveDot();
  }

  function loadAssets() {
    if (loaded) return loaded;
    loaded = new Promise(function (res, rej) {
      if (window.Terminal && window.FitAddon && window.WebglAddon &&
          window.WebLinksAddon && window.Unicode11Addon && window.SearchAddon &&
          window.ClipboardAddon) return res();
      var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = CSS; document.head.appendChild(l);
      var scripts = [XJS, FJS, WLJS, U11JS, SJS, CJS, WGLJS];
      var loadScript = function (i) {
        if (i >= scripts.length) return res();
        var s = document.createElement('script'); s.src = scripts[i];
        s.onload = function () { loadScript(i + 1); };
        s.onerror = rej; document.head.appendChild(s);
      };
      loadScript(0);
    });
    return loaded;
  }

  function ready(cb) {
    loadAssets().then(cb).catch(function () { fail('Failed to load xterm.js (local asset missing).'); });
  }

  function openTerminalLink(event, uri) {
    if (event && event.preventDefault) event.preventDefault();
    var a = document.createElement('a');
    a.href = uri;
    a.target = '_blank';
    a.rel = 'noopener';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function cssVar(name, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function terminalTheme() {
    return {
      background: cssVar('--term-background', '#10131f'),
      foreground: cssVar('--term-foreground', '#e6e4da'),
      cursor: cssVar('--term-cursor', '#d4a95c'),
      selectionBackground: cssVar('--term-selection-background', 'rgba(109,127,247,0.34)'),
      black: cssVar('--term-black', '#0b0e18'),
      red: cssVar('--term-red', '#e5635a'),
      green: cssVar('--term-green', '#35b899'),
      yellow: cssVar('--term-yellow', '#d4a95c'),
      blue: cssVar('--term-blue', '#8090f6'),
      magenta: cssVar('--term-magenta', '#b887e8'),
      cyan: cssVar('--term-cyan', '#35b0d8'),
      white: cssVar('--term-white', '#d8d6cb'),
      brightBlack: cssVar('--term-bright-black', '#5c627a'),
      brightRed: cssVar('--term-bright-red', '#ff7b72'),
      brightGreen: cssVar('--term-bright-green', '#56d6b8'),
      brightYellow: cssVar('--term-bright-yellow', '#f0c56d'),
      brightBlue: cssVar('--term-bright-blue', '#9daaff'),
      brightMagenta: cssVar('--term-bright-magenta', '#d3a4ff'),
      brightCyan: cssVar('--term-bright-cyan', '#5bd3ee'),
      brightWhite: cssVar('--term-bright-white', '#fffaf0')
    };
  }

  function clipboardApi() {
    if (!window.navigator || !navigator.clipboard) return null;
    return navigator.clipboard;
  }

  function writeClipboardText(text) {
    if (text == null) return null;
    var clip = clipboardApi();
    if (!clip || typeof clip.writeText !== 'function') return null;
    try {
      var result = clip.writeText(String(text));
      if (result && result.catch) return result.catch(function () {});
      return result;
    } catch (_) {
      return null;
    }
  }

  function readClipboardText(cb) {
    var clip = clipboardApi();
    if (!clip || typeof clip.readText !== 'function') return;
    try {
      var result = clip.readText();
      if (result && result.then) {
        result.then(function (text) { cb(text || ''); }).catch(function () {});
      }
    } catch (_) {}
  }

  function copyOnSelectEnabled() {
    try { return localStorage.getItem(COPY_SELECT_KEY) === '1'; } catch (_) { return false; }
  }

  function attachTerminalClipboardHandlers(term) {
    if (term.attachCustomKeyEventHandler) {
      term.attachCustomKeyEventHandler(function (e) {
        var key = String(e.key || '').toLowerCase();
        if (e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey && key === 'c') {
          if (term.hasSelection && term.hasSelection()) {
            writeClipboardText(term.getSelection ? term.getSelection() : '');
            return false;
          }
          return true;
        }
        if (e.ctrlKey && e.shiftKey && !e.altKey && !e.metaKey && key === 'v') {
          readClipboardText(function (text) {
            if (term.paste && text) term.paste(text);
          });
          return false;
        }
        return true;
      });
    }
    if (term.onSelectionChange) {
      var lastSelection = '';
      term.onSelectionChange(function () {
        var selection = term.getSelection ? term.getSelection() : '';
        if (!selection) {
          lastSelection = '';
          return;
        }
        if (!copyOnSelectEnabled()) {
          lastSelection = '';
          return;
        }
        if (selection === lastSelection) return;
        lastSelection = selection;
        writeClipboardText(selection);
      });
    }
  }

  function writeOnlyClipboardProvider() {
    return {
      readText: function () { return ''; },
      writeText: function (selection, text) {
        if (selection !== 'c') return null;
        return writeClipboardText(text);
      }
    };
  }

  function loadRuntimeAddons(tab, term) {
    try { term.loadAddon(new WebLinksAddon.WebLinksAddon(openTerminalLink)); } catch (_) {}
    try {
      term.loadAddon(new Unicode11Addon.Unicode11Addon());
      if (term.unicode) term.unicode.activeVersion = '11';
    } catch (_) {}
    try {
      tab.search = new SearchAddon.SearchAddon();
      term.loadAddon(tab.search);
    } catch (_) { tab.search = null; }
    try {
      tab.clipboard = new ClipboardAddon.ClipboardAddon(
        new ClipboardAddon.Base64(),
        writeOnlyClipboardProvider()
      );
      term.loadAddon(tab.clipboard);
    } catch (_) { tab.clipboard = null; }
    try {
      var webgl = new WebglAddon.WebglAddon();
      tab.webgl = webgl;
      if (webgl.onContextLoss) {
        webgl.onContextLoss(function () {
          try { webgl.dispose(); } catch (_) {}
          if (tab.webgl === webgl) tab.webgl = null;
        });
      }
      term.loadAddon(webgl);
    } catch (_) {
      try { if (tab.webgl) tab.webgl.dispose(); } catch (__) {}
      tab.webgl = null;
    }
  }

  function ensureRuntime(tab) {
    if (tab.term) return;
    var screen = document.createElement('div');
    screen.className = 'term-screen';
    screen.hidden = tab.id !== activeId;
    screenHost.appendChild(screen);

    var term = new Terminal({
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
      fontSize: 13, cursorBlink: true, scrollback: 5000,
      theme: terminalTheme()
    });
    var fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(screen);
    loadRuntimeAddons(tab, term);
    attachTerminalClipboardHandlers(term);
    term.onData(function (d) {
      if (tab.ws && tab.ws.readyState === 1) tab.ws.send(enc.encode(d));
    });
    if (term.onTitleChange) {
      term.onTitleChange(function (title) {
        var next = cleanTitle(title, tab.title);
        if (next && next !== tab.title) {
          tab.title = next;
          persistTabs();
          renderTabs();
        }
      });
    }
    tab.screen = screen;
    tab.term = term;
    tab.fit = fit;
    tab.ro = new ResizeObserver(function () { refitTab(tab); });
    tab.ro.observe(screen);
    refitTab(tab);
  }

  function connectTab(tab) {
    if (tab.ws && (tab.ws.readyState === 0 || tab.ws.readyState === 1)) return;
    var proto = location.protocol === 'https:' ? 'wss' : 'ws';
    // Send both: sid reattaches; lesson only matters when the server has to create
    // a session (first connect, or healing a reaped sid back into the lesson dir).
    var qs = [];
    if (tab.sid) qs.push('sid=' + encodeURIComponent(tab.sid));
    if (tab.lesson) qs.push('lesson=' + encodeURIComponent(tab.lesson));
    var url = proto + '://' + location.host + '/terminal/ws' + (qs.length ? '?' + qs.join('&') : '');
    tab.ws = new WebSocket(url);
    tab.ws.binaryType = 'arraybuffer';
    tab.sentRows = 0; tab.sentCols = 0;
    tab.ws.onopen = function () {
      updateActiveDot();
      refitTab(tab);
      if (tab.id === activeId && tab.term) tab.term.focus();
    };
    tab.ws.onmessage = function (e) {
      if (typeof e.data === 'string') {
        try {
          var m = JSON.parse(e.data);
          if (m && m.type === 'session' && m.sid) {
            tab.sid = m.sid;
            persistTabs();
          }
        } catch (_) {}
        return;
      }
      if (tab.term) tab.term.write(new Uint8Array(e.data));
    };
    tab.ws.onclose = function () {
      if (tab.ws && tab.ws.readyState >= 2) tab.ws = null;
      updateActiveDot();
    };
    tab.ws.onerror = function () { fail('WebSocket error — the terminal is localhost-only.'); };
  }

  function connectAllTabs() {
    tabs.forEach(function (tab) {
      // Lesson tabs stay visible everywhere but only auto-connect on Learn;
      // elsewhere an explicit click still connects them via switchTab().
      if (tab.lesson && !onLearn()) return;
      ensureRuntime(tab);
      connectTab(tab);
    });
  }

  function updateActiveDot() {
    var tab = activeTab();
    if (!dotEl) return;
    dotEl.classList.toggle('on', !!(tab && tab.ws && tab.ws.readyState === 1));
  }

  function sendResize(tab) {
    if (!tab || tab.id !== activeId || !tab.term || !tab.ws || tab.ws.readyState !== 1) return;
    if (tab.term.rows === tab.sentRows && tab.term.cols === tab.sentCols) return;
    tab.sentRows = tab.term.rows; tab.sentCols = tab.term.cols;
    tab.ws.send(JSON.stringify({ type: 'resize', rows: tab.term.rows, cols: tab.term.cols }));
  }

  function refitTab(tab) {
    if (!tab || tab.id !== activeId || drawer.hidden || drawer.classList.contains('minimized')) return;
    try { tab.fit.fit(); } catch (_) {}
    sendResize(tab);
  }

  function focusSoon() {
    setTimeout(function () {
      var tab = activeTab();
      refitTab(tab);
      if (tab && tab.term && !drawer.classList.contains('minimized')) tab.term.focus();
    }, 60);
  }

  function isDesktopRightDock() {
    return document.body.dataset.rail === 'learn' &&
      window.matchMedia &&
      window.matchMedia('(min-width: 861px)').matches;
  }

  function syncInset() {
    if (drawer.hidden) {
      document.body.classList.remove('term-open', 'term-right-open');
      document.body.style.removeProperty('--term-h');
      document.body.style.removeProperty('--term-w');
      return;
    }
    var right = isDesktopRightDock();
    document.body.classList.add('term-open');
    document.body.classList.toggle('term-right-open', right);
    if (right) {
      document.body.style.removeProperty('--term-h');
      document.body.style.setProperty('--term-w', drawer.offsetWidth + 'px');
    } else {
      document.body.classList.remove('term-right-open');
      document.body.style.removeProperty('--term-w');
      document.body.style.setProperty('--term-h', drawer.offsetHeight + 'px');
    }
  }

  function applyDock() {
    var right = isDesktopRightDock();
    drawer.classList.toggle('right-dock', right);
    if (right) {
      drawer.style.height = '';
      var w = localStorage.getItem(W_KEY);
      if (w) drawer.style.width = w;
    } else {
      drawer.style.width = '';
      var h = localStorage.getItem(H_KEY);
      if (h) drawer.style.height = h;
    }
    syncInset();
  }

  function open() {
    drawer.hidden = false;
    drawer.setAttribute('aria-hidden', 'false');
    drawer.classList.toggle('minimized', localStorage.getItem(MIN_KEY) === '1');
    toggle.classList.add('active');
    localStorage.setItem(OPEN_KEY, '1');
    clearFail();
    ensureDefaultTab();
    applyDock();
    renderTabs();
    ready(function () {
      connectAllTabs();
      focusSoon();
    });
  }

  function hide() {
    drawer.hidden = true;
    drawer.setAttribute('aria-hidden', 'true');
    toggle.classList.remove('active');
    localStorage.setItem(OPEN_KEY, '0');
    syncInset();
  }

  function switchTab(id) {
    if (!tabs.some(function (t) { return t.id === id; })) return;
    setActive(id);
    clearFail();
    tabs.forEach(function (tab) {
      if (tab.screen) tab.screen.hidden = tab.id !== activeId;
    });
    persistTabs();
    renderTabs();
    if (!drawer.hidden) {
      ready(function () {
        var tab = activeTab();
        ensureRuntime(tab);
        connectTab(tab);
        focusSoon();
      });
    }
  }

  function createTab() {
    if (tabs.length >= MAX_TABS) {
      fail('[terminal: maximum 8 sessions]');
      return;
    }
    var tab = {
      id: newId(), sid: null, lesson: null, title: 'Terminal ' + (tabs.length + 1),
      term: null, fit: null, search: null, clipboard: null, webgl: null, ws: null, screen: null, ro: null,
      sentRows: 0, sentCols: 0
    };
    tabs.push(tab);
    setActive(tab.id);
    clearFail();
    persistTabs();
    renderTabs();
    if (drawer.hidden) open();
    else switchTab(tab.id);
  }

  function openLessonTab(slug, title) {
    slug = String(slug || '').slice(0, 80);
    if (!slug) return;
    var tab = tabs.find(function (t) { return t.lesson === slug; });
    if (!tab) {
      if (tabs.length >= MAX_TABS) {
        fail('[terminal: maximum 8 sessions]');
        return;
      }
      tab = {
        id: newId(), sid: null, lesson: slug, title: cleanTitle(title, slug),
        term: null, fit: null, search: null, clipboard: null, webgl: null, ws: null, screen: null, ro: null,
        sentRows: 0, sentCols: 0
      };
      tabs.push(tab);
    }
    setActive(tab.id);
    clearFail();
    persistTabs();
    renderTabs();
    if (drawer.hidden) open();
    else switchTab(tab.id);
    if (drawer.classList.contains('minimized')) setMinimized(false);
  }

  function closeActiveTab() {
    var tab = activeTab();
    if (!tab) {
      hide();
      return;
    }
    try {
      if (tab.ws && tab.ws.readyState === 1) tab.ws.send(JSON.stringify({ type: 'kill' }));
    } catch (_) {}
    try { if (tab.ws) tab.ws.close(); } catch (_) {}
    if (tab.ro) tab.ro.disconnect();
    try { if (tab.clipboard) tab.clipboard.dispose(); } catch (_) {}
    try { if (tab.webgl) tab.webgl.dispose(); } catch (_) {}
    try { if (tab.term) tab.term.dispose(); } catch (_) {}
    if (tab.screen) tab.screen.remove();

    var idx = tabs.indexOf(tab);
    tabs.splice(idx, 1);
    setActive(tabs[Math.max(0, idx - 1)] ? tabs[Math.max(0, idx - 1)].id : null);
    persistTabs();
    renderTabs();
    if (!tabs.length) {
      hide();
      return;
    }
    switchTab(activeId);
  }

  function setMinimized(min) {
    drawer.classList.toggle('minimized', min);
    localStorage.setItem(MIN_KEY, min ? '1' : '0');
    syncInset();
    if (!min) focusSoon();
  }

  function clamp(n, min, max) {
    return Math.max(min, Math.min(max, n));
  }

  function setDrawerSize(px) {
    if (isDesktopRightDock()) {
      var rail = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--rail-w'), 10) || 50;
      var maxW = Math.max(300, window.innerWidth - rail - 220);
      var w = clamp(px, 300, maxW);
      drawer.style.width = w + 'px';
      localStorage.setItem(W_KEY, drawer.style.width);
    } else {
      var h = clamp(px, 120, window.innerHeight - 80);
      drawer.style.height = h + 'px';
      localStorage.setItem(H_KEY, drawer.style.height);
    }
    syncInset();
  }

  function adjustSize(dir) {
    if (drawer.hidden || drawer.classList.contains('minimized')) return;
    var step = Math.round((isDesktopRightDock() ? window.innerWidth : window.innerHeight) * 0.08);
    setDrawerSize((isDesktopRightDock() ? drawer.offsetWidth : drawer.offsetHeight) + dir * step);
    focusSoon();
  }

  function resetSize() {
    if (isDesktopRightDock()) {
      drawer.style.width = '';
      localStorage.removeItem(W_KEY);
    } else {
      drawer.style.height = '';
      localStorage.removeItem(H_KEY);
    }
    syncInset();
    focusSoon();
  }

  function drawerHasFocus() {
    return !drawer.hidden && drawer.contains(document.activeElement);
  }

  function openFind() {
    if (!findEl || !findInput || drawer.hidden) return;
    findEl.hidden = false;
    drawer.classList.add('find-open');
    setTimeout(function () { findInput.focus(); findInput.select(); }, 0);
  }

  function closeFind(refocus) {
    if (!findEl) return;
    findEl.hidden = true;
    drawer.classList.remove('find-open');
    if (refocus) focusSoon();
  }

  function toggleFind() {
    if (!findEl || findEl.hidden) openFind();
    else closeFind(true);
  }

  function runSearch(next) {
    var tab = activeTab();
    var q = findInput ? findInput.value : '';
    if (!tab || !tab.search || !q) return;
    try {
      if (next) tab.search.findNext(q);
      else tab.search.findPrevious(q);
    } catch (_) {}
  }

  toggle.addEventListener('click', function () { drawer.hidden ? open() : hide(); });
  if (newBtn) newBtn.addEventListener('click', createTab);
  var lessonBtn = document.getElementById('lesson-term-btn');
  if (lessonBtn) {
    lessonBtn.addEventListener('click', function () {
      openLessonTab(lessonBtn.dataset.lesson, lessonBtn.dataset.lessonTitle);
    });
  }
  if (findPrevBtn) findPrevBtn.addEventListener('click', function () { runSearch(false); });
  if (findNextBtn) findNextBtn.addEventListener('click', function () { runSearch(true); });
  if (findCloseBtn) findCloseBtn.addEventListener('click', function () { closeFind(true); });
  var killBtn = document.getElementById('term-close');
  if (killBtn) killBtn.addEventListener('click', closeActiveTab);
  var minBtn = document.getElementById('term-min');
  if (minBtn) minBtn.addEventListener('click', function () {
    setMinimized(!drawer.classList.contains('minimized'));
  });

  var handle = document.getElementById('term-resize');
  if (handle) {
    var onDrag = function (e) {
      if (isDesktopRightDock()) setDrawerSize(window.innerWidth - e.clientX);
      else setDrawerSize(window.innerHeight - e.clientY);
    };
    var endDrag = function () {
      window.removeEventListener('mousemove', onDrag);
      window.removeEventListener('mouseup', endDrag);
      document.body.style.userSelect = '';
      focusSoon();
    };
    handle.addEventListener('mousedown', function (e) {
      e.preventDefault();
      document.body.style.userSelect = 'none';
      window.addEventListener('mousemove', onDrag);
      window.addEventListener('mouseup', endDrag);
    });
  }

  window.addEventListener('keydown', function (e) {
    if (e.ctrlKey && e.shiftKey && !e.altKey && !e.metaKey &&
        String(e.key).toLowerCase() === 'f' && drawerHasFocus()) {
      e.preventDefault();
      e.stopPropagation();
      toggleFind();
      return;
    }
    if (findEl && !findEl.hidden && findEl.contains(document.activeElement)) {
      if (e.key === 'Enter') {
        e.preventDefault();
        e.stopPropagation();
        runSearch(!e.shiftKey);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        closeFind(true);
        return;
      }
    }
    if (e.ctrlKey && !e.altKey && !e.metaKey && e.key === '`') {
      e.preventDefault();
      drawer.hidden ? open() : hide();
      return;
    }
    if (!e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key >= '1' && e.key <= '8' && !drawer.hidden) {
      var tab = tabs[Number(e.key) - 1];
      if (tab) {
        e.preventDefault();
        e.stopPropagation();
        switchTab(tab.id);
      }
    } else if ((e.key === '=' || e.key === '+') && !drawer.hidden) {
      e.preventDefault(); e.stopPropagation(); adjustSize(1);
    } else if (e.key === '-' && !drawer.hidden) {
      e.preventDefault(); e.stopPropagation(); adjustSize(-1);
    } else if (e.key === '0' && !drawer.hidden) {
      e.preventDefault(); e.stopPropagation(); resetSize();
    } else if (e.key === '\\') {
      e.preventDefault(); e.stopPropagation();
      if (drawer.hidden) open();
      else setMinimized(!drawer.classList.contains('minimized'));
    }
  }, true);

  window.addEventListener('resize', function () {
    if (!drawer.hidden) {
      applyDock();
      focusSoon();
    }
  });

  readStoredTabs();
  renderTabs();
  if (localStorage.getItem(OPEN_KEY) === '1') open();
})();
