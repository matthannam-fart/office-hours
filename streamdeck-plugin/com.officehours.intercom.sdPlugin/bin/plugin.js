/// Office Hours — Stream Deck Plugin
/// Connects to the OH app via ws://localhost:50003
/// Mirrors stream_deck_manager.py behavior exactly.

const WebSocket = require("ws");

// ── Colors (match stream_deck_manager.py) ───────────────────
const OH_TEAL     = "#71ada3";
const OH_TEAL_DIM = "#283c3c";
const COLOR_OFF   = "#000000";
const COLOR_GREEN  = "#008c3c";
const COLOR_YELLOW = "#b48c00";
const COLOR_RED    = "#b4281e";

const MODE_LABELS = { GREEN: "AVAIL", YELLOW: "BUSY", RED: "DND" };
const MODE_COLORS = { GREEN: COLOR_GREEN, YELLOW: COLOR_YELLOW, RED: COLOR_RED };

// ── Stream Deck SDK bootstrap ───────────────────────────────
let sdWs = null;
let sdPort, sdUUID, sdRegisterEvent, sdInfo;
const contexts = {}; // actionUUID → Set of context strings

function parseArgs() {
    const args = process.argv;
    for (let i = 0; i < args.length; i++) {
        if (args[i] === "-port") sdPort = parseInt(args[++i]);
        if (args[i] === "-pluginUUID") sdUUID = args[++i];
        if (args[i] === "-registerEvent") sdRegisterEvent = args[++i];
        if (args[i] === "-info") sdInfo = JSON.parse(args[++i]);
    }
}

function connectToSD() {
    sdWs = new WebSocket(`ws://127.0.0.1:${sdPort}`);
    sdWs.on("open", () => {
        sdWs.send(JSON.stringify({ event: sdRegisterEvent, uuid: sdUUID }));
        log("Connected to Stream Deck app");
    });
    sdWs.on("message", (data) => {
        try {
            handleSDEvent(JSON.parse(data.toString()));
        } catch (e) {
            log("SD parse error: " + e.message);
        }
    });
    sdWs.on("close", () => { log("SD WebSocket closed"); });
    sdWs.on("error", (e) => { log("SD WebSocket error: " + e.message); });
}

// ── OH app connection ───────────────────────────────────────
let ohWs = null;
let ohState = {
    mode: "GREEN",
    talk: "idle",           // idle | live | rec | listen
    message: false,
    teams: [],
    users: [],
    activeTeamId: "",
    activeUserId: "",
    connected: false,
    peerName: "",
    preview: "",            // Name being browsed (shown temporarily)
    browseTeamIndex: 0,
    browseUserIndex: 0,
};
let reconnectTimer = null;

// Message pulse state (matches stream_deck_manager 0.6s interval)
let msgPulseOn = false;
let msgPulseTimer = null;

function connectToOH() {
    if (ohWs) {
        try { ohWs.close(); } catch (_) {}
    }
    try {
        ohWs = new WebSocket("ws://127.0.0.1:50003");
    } catch (_) {
        scheduleReconnect();
        return;
    }
    ohWs.on("open", () => {
        log("Connected to Office Hours app");
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
        refreshAllButtons();
    });
    ohWs.on("message", (data) => {
        try {
            const msg = JSON.parse(data.toString());
            if (msg.type === "state") {
                const oldMsg = ohState.message;
                Object.assign(ohState, msg);
                delete ohState.type;
                refreshAllButtons();
                // Handle message pulse start/stop
                if (ohState.message && !oldMsg) startMsgPulse();
                if (!ohState.message && oldMsg) stopMsgPulse();
            }
        } catch (e) {
            log("OH parse error: " + e.message);
        }
    });
    ohWs.on("close", () => {
        log("Office Hours disconnected");
        ohWs = null;
        stopMsgPulse();
        scheduleReconnect();
        showDisconnected();
    });
    ohWs.on("error", () => {
        ohWs = null;
        scheduleReconnect();
    });
}

function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectToOH();
    }, 3000);
}

function sendToOH(msg) {
    if (ohWs && ohWs.readyState === WebSocket.OPEN) {
        ohWs.send(JSON.stringify(msg));
    }
}

// ── Message pulse (0.6s interval, matches stream_deck_manager) ──
function startMsgPulse() {
    if (msgPulseTimer) return;
    msgPulseOn = true;
    msgPulseTimer = setInterval(() => {
        msgPulseOn = !msgPulseOn;
        // Pulse the logo key (matches stream_deck_manager KEY_LOGO pulse)
        refreshActionButtons("com.officehours.intercom.logo");
    }, 600);
}

function stopMsgPulse() {
    if (msgPulseTimer) { clearInterval(msgPulseTimer); msgPulseTimer = null; }
    msgPulseOn = false;
    refreshActionButtons("com.officehours.intercom.logo");
}

function refreshActionButtons(action) {
    const ctxSet = contexts[action];
    if (!ctxSet) return;
    for (const ctx of ctxSet) {
        refreshButton(action, ctx);
    }
}

// ── SD event handling ───────────────────────────────────────
function handleSDEvent(evt) {
    const action = evt.action;
    const ctx = evt.context;

    switch (evt.event) {
        case "willAppear":
            if (!contexts[action]) contexts[action] = new Set();
            contexts[action].add(ctx);
            refreshButton(action, ctx);
            break;
        case "willDisappear":
            if (contexts[action]) contexts[action].delete(ctx);
            break;
        case "keyDown":
            onKeyDown(action, ctx);
            break;
        case "keyUp":
            onKeyUp(action, ctx);
            break;
        case "systemDidWakeUp":
            connectToOH();
            break;
    }
}

function onKeyDown(action, ctx) {
    switch (action) {
        case "com.officehours.intercom.talk":
            sendToOH({ action: "ptt_press" });
            break;
        case "com.officehours.intercom.mode":
            sendToOH({ action: "cycle_mode" });
            break;
        case "com.officehours.intercom.team":
            sendToOH({ action: "cycle_team" });
            break;
        case "com.officehours.intercom.user":
            sendToOH({ action: "cycle_user" });
            break;
        case "com.officehours.intercom.panel":
            sendToOH({ action: "show_panel" });
            break;
    }
}

function onKeyUp(action, ctx) {
    if (action === "com.officehours.intercom.talk") {
        sendToOH({ action: "ptt_release" });
    }
}

// ── SVG rendering (matches stream_deck_manager.py visuals) ──
function setImage(action, ctx, svg) {
    if (!sdWs || sdWs.readyState !== WebSocket.OPEN) return;
    const encoded = "data:image/svg+xml;charset=utf8," + encodeURIComponent(svg);
    sdWs.send(JSON.stringify({
        event: "setImage",
        context: ctx,
        payload: { image: encoded, target: 0, state: 0 }
    }));
}

function renderSVG(bgColor, lines, fontSize) {
    fontSize = fontSize || 22;
    const w = 144, h = 144;
    const lineHeight = fontSize + 8;
    const totalHeight = lines.length * lineHeight;
    const startY = (h - totalHeight) / 2 + fontSize;

    // Text color: black on bright backgrounds, white on dark
    const r = parseInt(bgColor.slice(1, 3), 16);
    const g = parseInt(bgColor.slice(3, 5), 16);
    const b = parseInt(bgColor.slice(5, 7), 16);
    const textColor = (r > 128 && g > 128) ? "#000" : "#fff";

    let textEls = "";
    lines.forEach((line, i) => {
        const y = startY + i * lineHeight;
        textEls += `<text x="${w/2}" y="${y}" text-anchor="middle" `
            + `font-family="Helvetica, Arial, sans-serif" font-size="${fontSize}" `
            + `font-weight="bold" fill="${textColor}">${escapeXml(line)}</text>`;
    });

    return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">`
        + `<rect width="${w}" height="${h}" rx="12" fill="${bgColor}"/>`
        + textEls + `</svg>`;
}

function renderOHLogo() {
    const w = 144, h = 144;
    const cx = w / 2, cy = h / 2, r = 56;
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">`
        + `<rect width="${w}" height="${h}" fill="${COLOR_OFF}"/>`
        + `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${OH_TEAL}"/>`
        + `<text x="${cx}" y="${cy + 12}" text-anchor="middle" `
        + `font-family="Helvetica, Arial, sans-serif" font-size="34" `
        + `font-weight="bold" fill="white">OH</text></svg>`;
}

function truncName(name, max) {
    max = max || 6;
    if (!name) return "?";
    if (name.length > max) return name.slice(0, max - 1) + ".";
    return name;
}

function escapeXml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ── Button refresh (mirrors stream_deck_manager key layout) ─
function refreshAllButtons() {
    for (const [action, ctxSet] of Object.entries(contexts)) {
        for (const ctx of ctxSet) {
            refreshButton(action, ctx);
        }
    }
}

function refreshButton(action, ctx) {
    const offline = !ohWs || ohWs.readyState !== WebSocket.OPEN;

    switch (action) {
        // ── Key 0: PTT ──────────────────────────────────────
        case "com.officehours.intercom.talk": {
            let bg, lines;
            if (offline) {
                bg = OH_TEAL_DIM; lines = ["PUSH", "TO", "TALK"];
            } else if (ohState.talk === "live") {
                bg = COLOR_RED; lines = ["LIVE"];
            } else if (ohState.talk === "rec") {
                bg = COLOR_RED; lines = ["REC"];
            } else if (ohState.talk === "listen") {
                bg = OH_TEAL_DIM; lines = ["LISTEN"];
            } else {
                bg = OH_TEAL; lines = ["PUSH", "TO", "TALK"];
            }
            setImage(action, ctx, renderSVG(bg, lines));
            break;
        }

        // ── Key 1: Mode ─────────────────────────────────────
        case "com.officehours.intercom.mode": {
            if (offline) {
                setImage(action, ctx, renderSVG(OH_TEAL_DIM, ["MODE"]));
            } else {
                const label = MODE_LABELS[ohState.mode] || "AVAIL";
                const color = MODE_COLORS[ohState.mode] || COLOR_GREEN;
                setImage(action, ctx, renderSVG(color, [label]));
            }
            break;
        }

        // ── Key 2: OH Logo / Directory / MSG pulse ──────────
        case "com.officehours.intercom.logo": {
            if (offline) {
                setImage(action, ctx, renderOHLogo());
            } else if (ohState.message && msgPulseOn) {
                setImage(action, ctx, renderSVG(OH_TEAL, ["MSG"]));
            } else if (ohState.preview) {
                // Show preview name while browsing teams/users
                setImage(action, ctx, renderSVG(OH_TEAL, [ohState.preview]));
            } else {
                setImage(action, ctx, renderOHLogo());
            }
            break;
        }

        // ── Team key ────────────────────────────────────────
        case "com.officehours.intercom.team": {
            if (offline || !ohState.teams || ohState.teams.length === 0) {
                setImage(action, ctx, renderSVG(OH_TEAL_DIM, ["TEAM", "--"]));
            } else {
                // Use browse index if previewing, otherwise find active
                let team;
                const idx = ohState.browseTeamIndex || 0;
                if (ohState.preview && idx < ohState.teams.length) {
                    team = ohState.teams[idx];
                } else {
                    team = ohState.teams.find(t => t.id === ohState.activeTeamId) || ohState.teams[0];
                }
                const name = truncName(team.name);
                const isActive = team.id === ohState.activeTeamId;
                const isBrowsing = !!ohState.preview;
                setImage(action, ctx, renderSVG(
                    (isActive || isBrowsing) ? OH_TEAL : OH_TEAL_DIM,
                    ["TEAM", name]
                ));
            }
            break;
        }

        // ── User key ────────────────────────────────────────
        case "com.officehours.intercom.user": {
            if (offline || !ohState.users || ohState.users.length === 0) {
                setImage(action, ctx, renderSVG(OH_TEAL_DIM, ["USER", "--"]));
            } else {
                let user;
                const idx = ohState.browseUserIndex || 0;
                if (ohState.preview && idx < ohState.users.length) {
                    user = ohState.users[idx];
                } else {
                    user = ohState.users.find(u => u.id === ohState.activeUserId) || ohState.users[0];
                }
                const name = truncName(user.name);
                const isActive = user.id === ohState.activeUserId;
                const isBrowsing = !!ohState.preview;
                setImage(action, ctx, renderSVG(
                    (isActive || isBrowsing) ? OH_TEAL : OH_TEAL_DIM,
                    ["USER", name]
                ));
            }
            break;
        }

        // ── Panel / MORE key ────────────────────────────────
        case "com.officehours.intercom.panel": {
            setImage(action, ctx, renderSVG(OH_TEAL_DIM, ["MORE"]));
            break;
        }
    }
}

function showDisconnected() {
    const dimButtons = {
        "com.officehours.intercom.talk":  ["PUSH", "TO", "TALK"],
        "com.officehours.intercom.mode":  ["MODE"],
        "com.officehours.intercom.team":  ["TEAM", "--"],
        "com.officehours.intercom.user":  ["USER", "--"],
        "com.officehours.intercom.panel": ["MORE"],
    };
    for (const [action, lines] of Object.entries(dimButtons)) {
        const ctxSet = contexts[action];
        if (!ctxSet) continue;
        for (const ctx of ctxSet) {
            setImage(action, ctx, renderSVG(OH_TEAL_DIM, lines));
        }
    }
    // Logo gets the OH logo (not dimmed text)
    const logoCtxs = contexts["com.officehours.intercom.logo"];
    if (logoCtxs) {
        for (const ctx of logoCtxs) {
            setImage("com.officehours.intercom.logo", ctx, renderOHLogo());
        }
    }
}

// ── Logging ─────────────────────────────────────────────────
function log(msg) {
    console.log(`[OH Plugin] ${msg}`);
}

// ── Main ────────────────────────────────────────────────────
parseArgs();
connectToSD();
setTimeout(connectToOH, 500);
