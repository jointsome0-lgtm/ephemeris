"use strict";
/* GENERATED-SOURCE NOTICE: app/static/terminal.js is emitted from this
 * file by `bun run build` (tsc, issue #42) and committed so deploy stays
 * zero-build. Edit THIS file and re-emit; never edit the .js by hand. */
// Desktop / localhost-only terminal drawer (GCP Cloud Shell style) — the client
// half of app/terminal.py. Loaded by base.html only for local clients; the markup
// (#term-drawer) is gated server-side the same way, and the websocket itself
// re-verifies the peer, so this file being world-readable under /static is fine.
(function () {
    /* The right stack (#131). On Learn both terminals live in ONE right column —
     * agent above, learner below, a draggable seam between them — so the page is
     * governed by a single axis. Off Learn nothing here applies: neither drawer
     * takes `right-dock`, so the bottom drawer and its `--term-h` content lift
     * behave exactly as before.
     *
     * The stack's width is ONE value with ONE owner: `--term-w` on <body> is the
     * width the learner asked for (or nothing, for the default), and style.css
     * derives `--term-stack-w` from it — clamped so the lesson column keeps its
     * floor. Nothing measures a drawer to publish it: a measurement would be the
     * clamp's OUTPUT and could not also be its input. */
    var STACK_W_KEY = 'al-term-w'; /* the agent dock's old key: widths carry over */
    /* Mirrors the floor in style.css (`--term-stack-w`): the dock yields first,
     * and the lesson column never reads narrower than 680px — plus the 60px of
     * content padding and grid gaps it reads inside. */
    var LESSON_FLOOR = 680 + 60;
    var DOCK_MIN = 300;
    /* What the seam may never take from the pane above it, and the smallest a
     * pane is worth drawing at all. */
    var AGENT_PANE_FLOOR = 160;
    var PANE_MIN = 120;
    function railWidth() {
        return parseInt(getComputedStyle(document.documentElement).getPropertyValue('--rail-w'), 10) || 50;
    }
    function maxStackWidth() {
        return Math.max(DOCK_MIN, window.innerWidth - railWidth() - LESSON_FLOOR);
    }
    /* Publish the stored stack width (or a caller's new one) as `--term-w`. */
    function applyStackWidth(px) {
        var value = null;
        if (typeof px === 'number') {
            value = Math.max(DOCK_MIN, Math.min(px, maxStackWidth())) + 'px';
            try {
                localStorage.setItem(STACK_W_KEY, value);
            }
            catch (_) { }
        }
        else {
            try {
                value = localStorage.getItem(STACK_W_KEY);
            }
            catch (_) {
                value = null;
            }
        }
        if (value)
            document.body.style.setProperty('--term-w', value);
        else
            document.body.style.removeProperty('--term-w');
    }
    function forgetStackWidth() {
        try {
            localStorage.removeItem(STACK_W_KEY);
        }
        catch (_) { }
        document.body.style.removeProperty('--term-w');
    }
    function syncTerminalInsets() {
        var agent = document.getElementById('term-drawer');
        var learner = document.getElementById('learner-term-drawer');
        var agentOpen = !!agent && !agent.hidden;
        var learnerOpen = !!learner && !learner.hidden;
        var agentRight = agentOpen && agent.classList.contains('right-dock');
        var learnerRight = learnerOpen && learner.classList.contains('right-dock');
        /* A pane in the stack is not a bottom drawer: it lifts nothing, which is
         * how `--term-h` dies on Learn while every other page keeps it. */
        var bottomHeight = (agentOpen && !agentRight ? agent.offsetHeight : 0)
            + (learnerOpen && !learnerRight ? learner.offsetHeight : 0);
        /* A minimized agent is a head bar the learner has to start below, and CSS
         * cannot measure it — the one height the stack publishes upward. It is
         * safe only in this state: an expanded agent's height is derived from the
         * learner's, so publishing it always would close a loop. */
        var agentMin = agentRight && agent.classList.contains('minimized');
        if (agentMin)
            document.body.style.setProperty('--term-agent-h', agent.offsetHeight + 'px');
        else
            document.body.style.removeProperty('--term-agent-h');
        document.body.classList.toggle('term-open', agentOpen || learnerOpen);
        document.body.classList.toggle('term-right-open', agentRight);
        document.body.classList.toggle('term-right-min', agentMin);
        document.body.classList.toggle('term-stack-open', agentRight || learnerRight);
        document.body.classList.toggle('learner-term-open', learnerOpen);
        if (bottomHeight)
            document.body.style.setProperty('--term-h', bottomHeight + 'px');
        else
            document.body.style.removeProperty('--term-h');
        /* Only ever cleared here: the width is an input, set by applyStackWidth. */
        if (!agentRight && !learnerRight)
            document.body.style.removeProperty('--term-w');
        /* The seam is fitted here because only this function sees both panes. A
         * learner that opened alone was clamped against a column it had to
         * itself; when the agent joins or grows back, that height has to give the
         * pane above its floor. Storage keeps what the learner asked for — this
         * decides what fits right now, and applyDock restores it on every resize. */
        if (learnerRight && agentRight && !agentMin) {
            var seamMax = Math.max(PANE_MIN, window.innerHeight - AGENT_PANE_FLOOR);
            var seam = parseInt(learner.style.height, 10);
            if (seam > seamMax)
                learner.style.height = seamMax + 'px';
        }
        /* ORDER MATTERS, and this is the whole of it: the classes, `--term-agent-h`
         * and the seam above decide how CSS lays the learner out, and the
         * offsetHeight below forces the layout that answers them. Measure first
         * and the agent would get its `bottom` from the pane's previous state —
         * a full-height learner it is about to stop being. */
        if (learnerOpen) {
            document.body.style.setProperty('--term-learner-h', learner.offsetHeight + 'px');
        }
        else {
            document.body.style.removeProperty('--term-learner-h');
        }
    }
    function agentPaneStacked() {
        var agent = document.getElementById('term-drawer');
        return !!agent && !agent.hidden && agent.classList.contains('right-dock');
    }
    function initSurface(config) {
        var drawer = document.getElementById(config.idPrefix + '-drawer');
        var toggle = document.getElementById(config.toggleId);
        if (!drawer || !toggle)
            return;
        var assetHost = document.getElementById('term-drawer');
        var CSS = assetHost.dataset.xtermCss, XJS = assetHost.dataset.xtermJs, FJS = assetHost.dataset.fitJs;
        var WGLJS = assetHost.dataset.webglJs, WLJS = assetHost.dataset.webLinksJs;
        var U11JS = assetHost.dataset.unicode11Js, SJS = assetHost.dataset.searchJs, CJS = assetHost.dataset.clipboardJs;
        var keyStem = config.kind === 'agent' ? 'al-term-' : 'al-term-learner-';
        var OPEN_KEY = keyStem + 'open';
        var TABS_KEY = keyStem + 'tabs';
        var ACTIVE_KEY = keyStem + 'active';
        var H_KEY = keyStem + 'h';
        var MIN_KEY = keyStem + 'min';
        var COPY_SELECT_KEY = keyStem + 'copyselect';
        var MAX_TABS = 8;
        var MAX_STORED_TABS = 64;
        var statusEl = document.getElementById(config.idPrefix + '-status');
        var dotEl = document.getElementById(config.idPrefix + '-dot');
        var screenHost = document.getElementById(config.idPrefix + '-screens');
        var tabsEl = document.getElementById(config.idPrefix + '-tabs');
        var newBtn = document.getElementById(config.idPrefix + '-new');
        var findEl = document.getElementById(config.idPrefix + '-find');
        var findInput = document.getElementById(config.idPrefix + '-find-input');
        var findPrevBtn = document.getElementById(config.idPrefix + '-find-prev');
        var findNextBtn = document.getElementById(config.idPrefix + '-find-next');
        var findCloseBtn = document.getElementById(config.idPrefix + '-find-close');
        var enc = new TextEncoder();
        var loaded = null;
        var tabs = [];
        var allTabs = [];
        // Two pointers: activeId is the effective in-memory active tab; storedActiveId
        // is the durable one — the only value persistTabs() ever writes. The off-Learn
        // lesson-tab fallback changes activeId alone, so incidental persists (title
        // change, sid arrival) can never leak the transient choice into storage.
        var activeId = null;
        var storedActiveId = null;
        var idSeq = 0;
        /* A command a lesson button asked to be typed into a tab that was not
         * connected yet (#136). It waits for that tab's socket and for nothing
         * else: a tab that never connects types nothing. */
        var pendingCommand = null;
        function onLearn() {
            return document.body.dataset.rail === 'learn';
        }
        function setActive(id) {
            activeId = id;
            storedActiveId = id;
        }
        function fail(m) {
            if (!statusEl)
                return;
            statusEl.hidden = false;
            statusEl.textContent = m;
        }
        function clearFail() {
            if (statusEl)
                statusEl.hidden = true;
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
            try {
                raw = JSON.parse(localStorage.getItem(TABS_KEY) || 'null');
            }
            catch (_) { }
            var storedTabs = Array.isArray(raw) ? raw : [];
            // Agent tabs retain their historical first-eight bound. Learner tabs are
            // ordered least-to-most recently selected by persistTabs(), so keep the
            // newest global window: the current lesson is appended last and must not
            // be discarded before current-lesson filtering on the next page load.
            storedTabs = config.kind === 'agent'
                ? storedTabs.slice(0, MAX_TABS) : storedTabs.slice(-MAX_STORED_TABS);
            allTabs = storedTabs.map(function (t, i) {
                return {
                    id: cleanTitle(t.id, newId()),
                    sid: t.sid ? String(t.sid) : null,
                    lesson: t.lesson ? String(t.lesson).slice(0, 80) : null,
                    title: cleanTitle(t.title, 'Terminal ' + (i + 1)),
                    role: null,
                    term: null, fit: null, search: null, clipboard: null, webgl: null, ws: null, screen: null, ro: null,
                    sentRows: 0, sentCols: 0
                };
            });
            tabs = config.kind === 'learner'
                ? allTabs.filter(function (t) { return t.lesson === config.currentLesson; }).slice(0, MAX_TABS)
                : allTabs;
            storedActiveId = localStorage.getItem(ACTIVE_KEY);
            if (config.kind === 'agent' && !tabs.some(function (t) { return t.id === storedActiveId; })) {
                storedActiveId = tabs[0] ? tabs[0].id : null;
            }
            activeId = tabs.some(function (t) { return t.id === storedActiveId; })
                ? storedActiveId : (tabs[0] ? tabs[0].id : null);
            if (allTabs.length)
                persistTabs();
            // A lesson tab must not be auto-active outside Learn: fall back to the
            // first plain tab (creating one in memory if every stored tab is a lesson
            // tab). Only the active *pointer* is transient — storedActiveId still names
            // the lesson tab, so Learn restores it. The created tab itself becomes
            // durable with the first persist after it gains a live session; dropping
            // it instead would orphan a fresh PTY on every navigation.
            var act = activeTab();
            if (act && act.lesson && !onLearn()) {
                var plain = tabs.find(function (t) { return !t.lesson; });
                if (!plain && tabs.length < MAX_TABS) {
                    plain = {
                        id: newId(), sid: null, lesson: null, title: 'Terminal ' + (tabs.length + 1),
                        role: null,
                        term: null, fit: null, search: null, clipboard: null, webgl: null, ws: null, screen: null, ro: null,
                        sentRows: 0, sentCols: 0
                    };
                    tabs.push(plain);
                }
                if (plain)
                    activeId = plain.id;
            }
        }
        function persistTabs() {
            if (config.kind === 'learner') {
                allTabs = allTabs.filter(function (t) { return t.lesson !== config.currentLesson; }).concat(tabs);
                if (allTabs.length > MAX_STORED_TABS) {
                    allTabs = allTabs.slice(-MAX_STORED_TABS);
                }
            }
            else {
                allTabs = tabs;
            }
            localStorage.setItem(TABS_KEY, JSON.stringify(allTabs.map(function (t) {
                return { id: t.id, sid: t.sid, lesson: t.lesson || null, title: t.title };
            })));
            if (storedActiveId)
                localStorage.setItem(ACTIVE_KEY, storedActiveId);
            else
                localStorage.removeItem(ACTIVE_KEY);
        }
        function activeTab() {
            return tabs.find(function (t) { return t.id === activeId; }) || tabs[0] || null;
        }
        function ensureDefaultTab() {
            if (tabs.length)
                return;
            tabs.push({
                id: newId(), sid: null,
                lesson: config.kind === 'learner' ? config.currentLesson : null,
                title: config.kind === 'learner'
                    ? cleanTitle(config.currentLessonTitle, 'Learner 1') : 'Terminal 1',
                role: null,
                term: null, fit: null, search: null, clipboard: null, webgl: null, ws: null, screen: null, ro: null,
                sentRows: 0, sentCols: 0
            });
            setActive(tabs[0].id);
            persistTabs();
            renderTabs();
        }
        function renderTabs() {
            if (!tabsEl)
                return;
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
            if (newBtn)
                newBtn.disabled = tabs.length >= MAX_TABS;
            updateActiveDot();
        }
        function loadAssets() {
            if (loaded)
                return loaded;
            loaded = new Promise(function (res, rej) {
                if (window.Terminal && window.FitAddon && window.WebglAddon &&
                    window.WebLinksAddon && window.Unicode11Addon && window.SearchAddon &&
                    window.ClipboardAddon)
                    return res();
                var l = document.createElement('link');
                l.rel = 'stylesheet';
                l.href = CSS;
                document.head.appendChild(l);
                var scripts = [XJS, FJS, WLJS, U11JS, SJS, CJS, WGLJS];
                var loadScript = function (i) {
                    if (i >= scripts.length)
                        return res();
                    var s = document.createElement('script');
                    s.src = scripts[i];
                    s.onload = function () { loadScript(i + 1); };
                    s.onerror = rej;
                    document.head.appendChild(s);
                };
                loadScript(0);
            });
            return loaded;
        }
        function ready(cb) {
            loadAssets().then(cb).catch(function () { fail('Failed to load xterm.js (local asset missing).'); });
        }
        function openTerminalLink(event, uri) {
            if (event && event.preventDefault)
                event.preventDefault();
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
            if (!window.navigator || !navigator.clipboard)
                return null;
            return navigator.clipboard;
        }
        function writeClipboardText(text) {
            if (text == null)
                return null;
            var clip = clipboardApi();
            if (!clip || typeof clip.writeText !== 'function')
                return null;
            try {
                var result = clip.writeText(String(text));
                if (result && result.catch)
                    return result.catch(function () { });
                return result;
            }
            catch (_) {
                return null;
            }
        }
        function readClipboardText(cb) {
            var clip = clipboardApi();
            if (!clip || typeof clip.readText !== 'function')
                return;
            try {
                var result = clip.readText();
                if (result && result.then) {
                    result.then(function (text) { cb(text || ''); }).catch(function () { });
                }
            }
            catch (_) { }
        }
        function copyOnSelectEnabled() {
            try {
                return localStorage.getItem(COPY_SELECT_KEY) === '1';
            }
            catch (_) {
                return false;
            }
        }
        function attachTerminalClipboardHandlers(term) {
            if (term.attachCustomKeyEventHandler) {
                term.attachCustomKeyEventHandler(function (e) {
                    var key = String(e.key || '').toLowerCase();
                    // Ctrl+C copies only when something is selected — with no selection it
                    // must still reach the PTY as SIGINT. Ctrl+Shift+C is the explicit copy
                    // alias and takes the same path, so with no selection it also falls
                    // through exactly as it did before.
                    if (e.ctrlKey && !e.altKey && !e.metaKey && key === 'c') {
                        if (term.hasSelection && term.hasSelection()) {
                            writeClipboardText(term.getSelection ? term.getSelection() : '');
                            // Returning false stops xterm but not the browser: its _keyDown
                            // returns before the cancel() that would preventDefault. Plain
                            // Ctrl+C's default is the browser copying the same selection, so
                            // it stays as it was; the alias' default is the devtools
                            // inspector, which must not open on a copy.
                            if (e.shiftKey)
                                e.preventDefault();
                            return false;
                        }
                        return true;
                    }
                    if (e.ctrlKey && e.shiftKey && !e.altKey && !e.metaKey && key === 'v') {
                        readClipboardText(function (text) {
                            if (term.paste && text)
                                term.paste(text);
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
                    if (selection === lastSelection)
                        return;
                    lastSelection = selection;
                    writeClipboardText(selection);
                });
            }
        }
        function writeOnlyClipboardProvider() {
            return {
                readText: function () { return ''; },
                writeText: function (selection, text) {
                    if (selection !== 'c')
                        return null;
                    return writeClipboardText(text);
                }
            };
        }
        function loadRuntimeAddons(tab, term) {
            try {
                term.loadAddon(new WebLinksAddon.WebLinksAddon(openTerminalLink));
            }
            catch (_) { }
            try {
                term.loadAddon(new Unicode11Addon.Unicode11Addon());
                if (term.unicode)
                    term.unicode.activeVersion = '11';
            }
            catch (_) { }
            try {
                tab.search = new SearchAddon.SearchAddon();
                term.loadAddon(tab.search);
            }
            catch (_) {
                tab.search = null;
            }
            try {
                tab.clipboard = new ClipboardAddon.ClipboardAddon(new ClipboardAddon.Base64(), writeOnlyClipboardProvider());
                term.loadAddon(tab.clipboard);
            }
            catch (_) {
                tab.clipboard = null;
            }
            try {
                var webgl = new WebglAddon.WebglAddon();
                tab.webgl = webgl;
                if (webgl.onContextLoss) {
                    webgl.onContextLoss(function () {
                        try {
                            webgl.dispose();
                        }
                        catch (_) { }
                        if (tab.webgl === webgl)
                            tab.webgl = null;
                    });
                }
                term.loadAddon(webgl);
            }
            catch (_) {
                try {
                    if (tab.webgl)
                        tab.webgl.dispose();
                }
                catch (__) { }
                tab.webgl = null;
            }
        }
        function ensureRuntime(tab) {
            if (tab.term)
                return;
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
                if (tab.ws && tab.ws.readyState === 1)
                    tab.ws.send(enc.encode(d));
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
            if (tab.ws && (tab.ws.readyState === 0 || tab.ws.readyState === 1))
                return;
            var proto = location.protocol === 'https:' ? 'wss' : 'ws';
            var attaching = !!tab.sid;
            var receivedSession = false;
            // A role selector is creation-only: learner reattach sends SID (and the
            // inert lesson context) but never combines SID with role, which E3 refuses.
            var qs = [];
            if (tab.sid)
                qs.push('sid=' + encodeURIComponent(tab.sid));
            if (tab.lesson)
                qs.push('lesson=' + encodeURIComponent(tab.lesson));
            if (!tab.sid && config.kind === 'learner')
                qs.push('role=lesson-learner');
            var url = proto + '://' + location.host + '/terminal/ws' + (qs.length ? '?' + qs.join('&') : '');
            tab.ws = new WebSocket(url);
            tab.ws.binaryType = 'arraybuffer';
            tab.sentRows = 0;
            tab.sentCols = 0;
            tab.ws.onopen = function () {
                updateActiveDot();
                refitTab(tab);
                if (pendingCommand && pendingCommand.id === tab.id) {
                    typeCommand(tab, pendingCommand.text);
                }
                if (tab.id === activeId && tab.term)
                    tab.term.focus();
            };
            tab.ws.onmessage = function (e) {
                if (typeof e.data === 'string') {
                    try {
                        var m = JSON.parse(e.data);
                        if (typeof m === 'object' && m !== null
                            && m.type === 'session'
                            && typeof m.sid === 'string'
                            && (m.role === 'plain'
                                || m.role === 'lesson-agent'
                                || m.role === 'lesson-learner')) {
                            var role = m.role;
                            var roleFitsSurface = config.kind === 'learner'
                                ? role === 'lesson-learner' : role !== 'lesson-learner';
                            if (!roleFitsSurface) {
                                fail('[terminal: server role does not match this surface]');
                                tab.sid = null;
                                tab.role = null;
                                persistTabs();
                                tab.ws?.close();
                                return;
                            }
                            receivedSession = true;
                            tab.sid = m.sid;
                            tab.role = role;
                            persistTabs();
                        }
                    }
                    catch (_) { }
                    return;
                }
                if (tab.term)
                    tab.term.write(new Uint8Array(e.data));
            };
            tab.ws.onclose = function () {
                if (tab.ws && tab.ws.readyState >= 2)
                    tab.ws = null;
                /* Nothing was typed and this tab is no longer the one the learner
                 * clicked on — a later reconnect must not surprise them with it. */
                if (pendingCommand && pendingCommand.id === tab.id)
                    pendingCommand = null;
                // E3 refuses stale learner SID healing without an explicit role. Clear
                // only after a failed attach; the next deliberate click can create a new
                // learner session with the selector instead of looping on the stale SID.
                if (config.kind === 'learner' && attaching && !receivedSession) {
                    tab.sid = null;
                    tab.role = null;
                    persistTabs();
                }
                updateActiveDot();
            };
            tab.ws.onerror = function () { fail('WebSocket error — the terminal is localhost-only.'); };
        }
        function connectAllTabs() {
            if (config.kind === 'learner') {
                var active = activeTab();
                if (active) {
                    ensureRuntime(active);
                    connectTab(active);
                }
                return;
            }
            tabs.forEach(function (tab) {
                // Lesson tabs stay visible everywhere but only auto-connect on Learn;
                // elsewhere an explicit click still connects them via switchTab().
                if (tab.lesson && !onLearn())
                    return;
                ensureRuntime(tab);
                connectTab(tab);
            });
        }
        function updateActiveDot() {
            var tab = activeTab();
            if (!dotEl)
                return;
            dotEl.classList.toggle('on', !!(tab && tab.ws && tab.ws.readyState === 1));
        }
        function sendResize(tab) {
            if (!tab || tab.id !== activeId || !tab.term || !tab.ws || tab.ws.readyState !== 1)
                return;
            if (tab.term.rows === tab.sentRows && tab.term.cols === tab.sentCols)
                return;
            tab.sentRows = tab.term.rows;
            tab.sentCols = tab.term.cols;
            tab.ws.send(JSON.stringify({ type: 'resize', rows: tab.term.rows, cols: tab.term.cols }));
        }
        function refitTab(tab) {
            if (!tab || tab.id !== activeId || drawer.hidden || drawer.classList.contains('minimized'))
                return;
            try {
                tab.fit.fit();
            }
            catch (_) { }
            sendResize(tab);
        }
        function focusSoon() {
            setTimeout(function () {
                var tab = activeTab();
                refitTab(tab);
                if (tab && tab.term && !drawer.classList.contains('minimized'))
                    tab.term.focus();
            }, 60);
        }
        /* Learn, wide enough: both surfaces belong to the right stack. */
        function inRightStack() {
            return document.body.dataset.rail === 'learn' &&
                !!window.matchMedia &&
                window.matchMedia('(min-width: 861px)').matches;
        }
        /* Only the agent drags the stack's outer edge, and it drags a width. The
         * learner keeps dragging a height — in the stack that drag IS the seam. */
        function ownsStackWidth() {
            return config.kind === 'agent' && inRightStack();
        }
        function syncInset() {
            syncTerminalInsets();
        }
        function applyDock() {
            var stacked = inRightStack();
            drawer.classList.toggle('right-dock', stacked);
            /* The stack's width is shared, so it lives on <body>, not on one pane —
             * and either pane arriving alone still has to publish it. */
            if (stacked)
                applyStackWidth();
            if (ownsStackWidth()) {
                drawer.style.height = '';
                drawer.style.width = '';
            }
            else {
                drawer.style.width = '';
                /* Through the same clamp as a drag: a height stored on a taller window,
                 * or as a bottom drawer, would otherwise leave the pane above it nothing.
                 * Storage keeps the height the learner asked for — this only decides what
                 * fits here, and applyDock runs again on every resize. */
                var h = parseInt(localStorage.getItem(H_KEY) || '', 10);
                if (h > 0)
                    drawer.style.height = clampDrawerHeight(h) + 'px';
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
            if (!tabs.some(function (t) { return t.id === id; }))
                return;
            setActive(id);
            clearFail();
            tabs.forEach(function (tab) {
                if (tab.screen)
                    tab.screen.hidden = tab.id !== activeId;
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
                id: newId(), sid: null,
                lesson: config.kind === 'learner' ? config.currentLesson : null,
                title: (config.kind === 'learner' ? 'Learner ' : 'Terminal ') + (tabs.length + 1),
                role: null,
                term: null, fit: null, search: null, clipboard: null, webgl: null, ws: null, screen: null, ro: null,
                sentRows: 0, sentCols: 0
            };
            tabs.push(tab);
            setActive(tab.id);
            clearFail();
            persistTabs();
            renderTabs();
            if (drawer.hidden)
                open();
            else
                switchTab(tab.id);
        }
        /* Returns whether this call CREATED the session, which is the only state a
         * caller can safely write into: a reused tab may have an editor or an agent
         * in the foreground (#136 review round 2). */
        function openLessonTab(slug, title) {
            slug = String(slug || '').slice(0, 80);
            if (!slug)
                return false;
            var created = false;
            var tab = tabs.find(function (t) { return t.lesson === slug; });
            if (!tab) {
                if (tabs.length >= MAX_TABS) {
                    fail('[terminal: maximum 8 sessions]');
                    return false;
                }
                created = true;
                tab = {
                    id: newId(), sid: null, lesson: slug, title: cleanTitle(title, slug),
                    role: null,
                    term: null, fit: null, search: null, clipboard: null, webgl: null, ws: null, screen: null, ro: null,
                    sentRows: 0, sentCols: 0
                };
                tabs.push(tab);
            }
            setActive(tab.id);
            clearFail();
            persistTabs();
            renderTabs();
            if (drawer.hidden)
                open();
            else
                switchTab(tab.id);
            if (drawer.classList.contains('minimized'))
                setMinimized(false);
            return created;
        }
        /* Type a command into a tab and STOP at the prompt (#136).
         *
         * Not a new way to run anything: this is xterm's own paste path, the same
         * one the paste button and Ctrl+Shift+V use, so the text reaches the shell
         * as keystrokes over the tab's existing input socket and bracketed paste
         * keeps it inert. Line breaks are collapsed here as well — whatever a
         * caller hands over, what arrives is one line the learner still has to
         * press Enter on. */
        function typeCommand(tab, text) {
            if (!tab || !text)
                return;
            if (!tab.term || !tab.ws || tab.ws.readyState !== 1) {
                pendingCommand = { id: tab.id, text: text };
                return;
            }
            pendingCommand = null;
            if (tab.term.paste)
                tab.term.paste(text);
            focusSoon();
        }
        function closeActiveTab() {
            var tab = activeTab();
            if (!tab) {
                hide();
                return;
            }
            try {
                if (tab.ws && tab.ws.readyState === 1)
                    tab.ws.send(JSON.stringify({ type: 'kill' }));
            }
            catch (_) { }
            try {
                if (tab.ws)
                    tab.ws.close();
            }
            catch (_) { }
            if (tab.ro)
                tab.ro.disconnect();
            try {
                if (tab.clipboard)
                    tab.clipboard.dispose();
            }
            catch (_) { }
            try {
                if (tab.webgl)
                    tab.webgl.dispose();
            }
            catch (_) { }
            try {
                if (tab.term)
                    tab.term.dispose();
            }
            catch (_) { }
            if (tab.screen)
                tab.screen.remove();
            var idx = tabs.indexOf(tab);
            tabs.splice(idx, 1);
            // The implicit successor obeys the same off-Learn rule as boot: prefer a
            // plain tab, and never auto-connect a lesson tab the user didn't pick.
            var next = tabs[Math.max(0, idx - 1)] || null;
            if (next && next.lesson && !onLearn()) {
                next = tabs.find(function (t) { return !t.lesson; }) || next;
            }
            setActive(next ? next.id : null);
            persistTabs();
            renderTabs();
            if (!tabs.length) {
                hide();
                return;
            }
            if (next && next.lesson && !onLearn()) {
                // Only lesson tabs remain: show it selected but leave it disconnected.
                tabs.forEach(function (tab) {
                    if (tab.screen)
                        tab.screen.hidden = tab.id !== activeId;
                });
                return;
            }
            switchTab(activeId);
        }
        function setMinimized(min) {
            drawer.classList.toggle('minimized', min);
            localStorage.setItem(MIN_KEY, min ? '1' : '0');
            syncInset();
            if (!min)
                focusSoon();
        }
        function clamp(n, min, max) {
            return Math.max(min, Math.min(max, n));
        }
        /* In the stack the seam may not swallow the pane above it. Opening alone is
         * not an exemption, only a later measurement — syncTerminalInsets re-fits
         * the seam when the agent joins the column. */
        function clampDrawerHeight(px) {
            var floor = config.kind === 'learner' && inRightStack() && agentPaneStacked()
                ? AGENT_PANE_FLOOR : 80;
            return clamp(px, PANE_MIN, Math.max(PANE_MIN, window.innerHeight - floor));
        }
        function setDrawerSize(px) {
            if (ownsStackWidth()) {
                applyStackWidth(px);
            }
            else {
                drawer.style.height = clampDrawerHeight(px) + 'px';
                localStorage.setItem(H_KEY, drawer.style.height);
            }
            syncInset();
        }
        function adjustSize(dir) {
            if (drawer.hidden || drawer.classList.contains('minimized'))
                return;
            var wide = ownsStackWidth();
            var step = Math.round((wide ? window.innerWidth : window.innerHeight) * 0.08);
            setDrawerSize((wide ? drawer.offsetWidth : drawer.offsetHeight) + dir * step);
            focusSoon();
        }
        function resetSize() {
            if (ownsStackWidth()) {
                drawer.style.width = '';
                forgetStackWidth();
            }
            else {
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
            if (!findEl || !findInput || drawer.hidden)
                return;
            findEl.hidden = false;
            drawer.classList.add('find-open');
            setTimeout(function () { findInput.focus(); findInput.select(); }, 0);
        }
        function closeFind(refocus) {
            if (!findEl)
                return;
            findEl.hidden = true;
            drawer.classList.remove('find-open');
            if (refocus)
                focusSoon();
        }
        function toggleFind() {
            if (!findEl || findEl.hidden)
                openFind();
            else
                closeFind(true);
        }
        function runSearch(next) {
            var tab = activeTab();
            var q = findInput ? findInput.value : '';
            if (!tab || !tab.search || !q)
                return;
            try {
                if (next)
                    tab.search.findNext(q);
                else
                    tab.search.findPrevious(q);
            }
            catch (_) { }
        }
        toggle.addEventListener('click', function () { drawer.hidden ? open() : hide(); });
        if (newBtn)
            newBtn.addEventListener('click', createTab);
        var lessonBtn = config.lessonButtonId
            ? document.getElementById(config.lessonButtonId) : null;
        if (lessonBtn) {
            lessonBtn.addEventListener('click', function () {
                openLessonTab(lessonBtn.dataset.lesson, lessonBtn.dataset.lessonTitle);
            });
        }
        /* "Review my answers": the lesson's agent terminal, with the tutor command
         * already typed. Same tab the terminal icon opens — asking for a review is
         * not a second kind of session. */
        var reviewBtn = config.reviewButtonId
            ? document.getElementById(config.reviewButtonId) : null;
        if (reviewBtn) {
            reviewBtn.addEventListener('click', function () {
                var slug = String(reviewBtn.dataset.lesson || '').slice(0, 80);
                var text = String(reviewBtn.dataset.termCommand || '')
                    .replace(/[\r\n]+/g, ' ').slice(0, 400);
                var created = openLessonTab(slug, reviewBtn.dataset.lessonTitle);
                var tab = tabs.find(function (t) { return t.lesson === slug; }) || null;
                /* Only into a shell this click just started, and only if it is the tab
                 * on screen: at the tab ceiling openLessonTab refuses, and a session
                 * already open may have an editor or an agent in the foreground, which
                 * would swallow the text as content. So a reused session is brought
                 * forward and left alone — the learner types in it, or closes it and
                 * clicks again for a fresh one. */
                if (created && tab && tab.id === activeId)
                    typeCommand(tab, text);
                else if (tab && tab.id === activeId) {
                    fail('[terminal: session already open — command not typed]');
                }
            });
        }
        if (findPrevBtn)
            findPrevBtn.addEventListener('click', function () { runSearch(false); });
        if (findNextBtn)
            findNextBtn.addEventListener('click', function () { runSearch(true); });
        if (findCloseBtn)
            findCloseBtn.addEventListener('click', function () { closeFind(true); });
        var pasteBtn = document.getElementById(config.idPrefix + '-paste');
        if (pasteBtn) {
            pasteBtn.addEventListener('click', function () {
                var tab = activeTab();
                if (!tab || !tab.term)
                    return;
                // Same path as Ctrl+Shift+V: xterm's paste honours the app's bracketed
                // paste mode, so a clipboard string carrying a newline stays inert text
                // instead of executing as soon as it reaches the shell.
                readClipboardText(function (text) {
                    if (text && tab.term.paste)
                        tab.term.paste(text);
                });
                focusSoon();
            });
        }
        var killBtn = document.getElementById(config.idPrefix + '-close');
        if (killBtn)
            killBtn.addEventListener('click', closeActiveTab);
        var minBtn = document.getElementById(config.idPrefix + '-min');
        if (minBtn)
            minBtn.addEventListener('click', function () {
                setMinimized(!drawer.classList.contains('minimized'));
            });
        // The copy-on-select flag was already read per selection; this only makes it
        // reachable. Default stays off, and the state lives under the per-surface key.
        var copySelBtn = document.getElementById(config.idPrefix + '-copysel');
        if (copySelBtn) {
            var syncCopySelect = function () {
                var on = copyOnSelectEnabled();
                copySelBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
                copySelBtn.classList.toggle('active', on);
            };
            copySelBtn.addEventListener('click', function () {
                var next = !copyOnSelectEnabled();
                try {
                    localStorage.setItem(COPY_SELECT_KEY, next ? '1' : '0');
                }
                catch (_) { }
                syncCopySelect();
                focusSoon();
            });
            // Another document — the same drawer kind in a second tab — can flip the
            // flag, and selection handling re-reads it live. Without this the button
            // would keep showing this tab's stale answer. A null key is a clear().
            window.addEventListener('storage', function (e) {
                if (e.key && e.key !== COPY_SELECT_KEY)
                    return;
                syncCopySelect();
            });
            syncCopySelect();
        }
        var handle = document.getElementById(config.idPrefix + '-resize');
        if (handle) {
            var onDrag = function (e) {
                if (ownsStackWidth())
                    setDrawerSize(window.innerWidth - e.clientX);
                else
                    setDrawerSize(window.innerHeight - e.clientY);
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
            if (!config.keyboardShortcuts)
                return;
            if (e.ctrlKey && !e.altKey && !e.metaKey && e.key === '`') {
                e.preventDefault();
                drawer.hidden ? open() : hide();
                return;
            }
            if (!e.altKey || e.ctrlKey || e.metaKey)
                return;
            if (e.key >= '1' && e.key <= '8' && !drawer.hidden) {
                var tab = tabs[Number(e.key) - 1];
                if (tab) {
                    e.preventDefault();
                    e.stopPropagation();
                    switchTab(tab.id);
                }
            }
            else if ((e.key === '=' || e.key === '+') && !drawer.hidden) {
                e.preventDefault();
                e.stopPropagation();
                adjustSize(1);
            }
            else if (e.key === '-' && !drawer.hidden) {
                e.preventDefault();
                e.stopPropagation();
                adjustSize(-1);
            }
            else if (e.key === '0' && !drawer.hidden) {
                e.preventDefault();
                e.stopPropagation();
                resetSize();
            }
            else if (e.key === '\\') {
                e.preventDefault();
                e.stopPropagation();
                if (drawer.hidden)
                    open();
                else
                    setMinimized(!drawer.classList.contains('minimized'));
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
        if (config.restoreOpen && localStorage.getItem(OPEN_KEY) === '1')
            open();
    }
    var learnerToggle = document.getElementById('lesson-learner-term-btn');
    initSurface({
        kind: 'agent', idPrefix: 'term', toggleId: 'term-toggle',
        lessonButtonId: 'lesson-term-btn', reviewButtonId: 'lesson-review-btn',
        currentLesson: null,
        currentLessonTitle: null, restoreOpen: true, keyboardShortcuts: true
    });
    if (learnerToggle) {
        initSurface({
            kind: 'learner', idPrefix: 'learner-term',
            toggleId: 'lesson-learner-term-btn', lessonButtonId: null,
            reviewButtonId: null,
            currentLesson: learnerToggle.dataset.lesson || null,
            currentLessonTitle: learnerToggle.dataset.lessonTitle || null,
            restoreOpen: false, keyboardShortcuts: false
        });
    }
})();
