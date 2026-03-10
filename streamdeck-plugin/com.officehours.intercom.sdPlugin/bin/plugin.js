/// Office Hours — Stream Deck Plugin
/// Connects to the OH app via ws://localhost:50003
/// Mirrors OH app behavior exactly.

const WebSocket = require("ws");

// ── Colors (match OH app) ───────────────────
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
let reconnectDelay = 3000;  // Start at 3s, backoff to 30s max

// Message pulse state (matches OH app 0.6s interval)
let msgPulseOn = false;
let msgPulseTimer = null;

function connectToOH() {
    if (ohWs) {
        try { ohWs.close(); } catch (_) {}
    }
    try {
        ohWs = new WebSocket("ws://127.0.0.1:50003");
    } catch (e) {
        log("OH connection failed: " + e.message);
        scheduleReconnect();
        return;
    }
    ohWs.on("open", () => {
        log("Connected to Office Hours app");
        reconnectDelay = 3000;  // Reset backoff on success
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
    ohWs.on("error", (e) => {
        log("OH connection error: " + (e.message || e.code || "unknown"));
        ohWs = null;
        scheduleReconnect();
    });
}

function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectToOH();
    }, reconnectDelay);
    // Exponential backoff: 3s → 6s → 12s → 24s → 30s max
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
}

function sendToOH(msg) {
    if (ohWs && ohWs.readyState === WebSocket.OPEN) {
        ohWs.send(JSON.stringify(msg));
    }
}

// ── Message pulse (0.6s interval, matches OH app) ──
function startMsgPulse() {
    if (msgPulseTimer) return;
    msgPulseOn = true;
    msgPulseTimer = setInterval(() => {
        msgPulseOn = !msgPulseOn;
        // Pulse the logo key
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

// ── SVG rendering (matches OH app visuals) ──
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
    fontSize = fontSize || 26;
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

const OH_LOGO_B64 = "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";

function renderOHLogo() {
    const w = 144, h = 144;
    // Embed the actual OH logo PNG centered on dark background
    const imgW = 128, imgH = 120;
    const ix = (w - imgW) / 2, iy = (h - imgH) / 2;
    return `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${w}" height="${h}">`
        + `<rect width="${w}" height="${h}" fill="${COLOR_OFF}"/>`
        + `<image x="${ix}" y="${iy}" width="${imgW}" height="${imgH}" href="data:image/png;base64,${OH_LOGO_B64}"/>`
        + `</svg>`;
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

// ── Button refresh (mirrors OH app key layout) ─
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
