/// Vox — Stream Deck Plugin (SDK v2)
/// Connects to the Vox desktop app via ws://localhost:50003
/// Uses official @elgato/streamdeck SDK for Marketplace compatibility.

const { SingletonAction, streamDeck } = require("@elgato/streamdeck");
const WebSocket = require("ws");
const fs = require("fs");
const path = require("path");

// ── Colors (match Vox app) ──────────────────────────────────
const VOX_TEAL     = "#71ada3";
const VOX_TEAL_DIM = "#283c3c";
const COLOR_OFF    = "#000000";
const COLOR_GREEN  = "#008c3c";
const COLOR_YELLOW = "#b48c00";
const COLOR_RED    = "#b4281e";

const MODE_LABELS = { GREEN: "AVAIL", YELLOW: "BUSY", RED: "DND" };
const MODE_COLORS = { GREEN: COLOR_GREEN, YELLOW: COLOR_YELLOW, RED: COLOR_RED };

// ── Vox logo (embedded PNG base64) ──────────────────────────
const VOX_LOGO_B64 = fs.readFileSync(
    path.join(__dirname, "vox-logo.b64"), "utf8"
).trim();

// ── Vox app connection state ────────────────────────────────
let voxWs = null;
let voxState = {
    mode: "GREEN",
    talk: "idle",
    message: false,
    teams: [],
    users: [],
    activeTeamId: "",
    activeUserId: "",
    connected: false,
    peerName: "",
    preview: "",
    browseTeamIndex: 0,
    browseUserIndex: 0,
};
let reconnectTimer = null;
let reconnectDelay = 3000;

// Message pulse state (matches Vox app 0.6s interval)
let msgPulseOn = false;
let msgPulseTimer = null;

// ── Action instances (populated after registration) ─────────
let talkAction, modeAction, teamAction, userAction, logoAction, panelAction;

// ── SVG rendering ───────────────────────────────────────────

function renderSVG(bgColor, lines, fontSize) {
    fontSize = fontSize || 26;
    const w = 144, h = 144;
    const lineHeight = fontSize + 8;
    const totalHeight = lines.length * lineHeight;
    const startY = (h - totalHeight) / 2 + fontSize;

    const r = parseInt(bgColor.slice(1, 3), 16);
    const g = parseInt(bgColor.slice(3, 5), 16);
    const b = parseInt(bgColor.slice(5, 7), 16);
    const textColor = (r > 128 && g > 128) ? "#000" : "#fff";

    let textEls = "";
    lines.forEach((line, i) => {
        const y = startY + i * lineHeight;
        textEls += `<text x="${w / 2}" y="${y}" text-anchor="middle" `
            + `font-family="Helvetica, Arial, sans-serif" font-size="${fontSize}" `
            + `font-weight="bold" fill="${textColor}">${escapeXml(line)}</text>`;
    });

    return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">`
        + `<rect width="${w}" height="${h}" rx="12" fill="${bgColor}"/>`
        + textEls + `</svg>`;
}

function renderVoxLogo() {
    const w = 144, h = 144;
    const imgW = 128, imgH = 120;
    const ix = (w - imgW) / 2, iy = (h - imgH) / 2;
    return `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${w}" height="${h}">`
        + `<rect width="${w}" height="${h}" fill="${COLOR_OFF}"/>`
        + `<image x="${ix}" y="${iy}" width="${imgW}" height="${imgH}" href="data:image/png;base64,${VOX_LOGO_B64}"/>`
        + `</svg>`;
}

function svgDataUrl(svg) {
    return "data:image/svg+xml;charset=utf8," + encodeURIComponent(svg);
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

// ── Helpers to push images to all visible instances of an action ──

function setAllImages(actionInstance, svg) {
    const url = svgDataUrl(svg);
    for (const a of actionInstance.actions) {
        if (a.isKey()) {
            a.setImage(url);
        }
    }
}

// ── Per-action refresh logic ────────────────────────────────

function refreshTalk() {
    const offline = !voxWs || voxWs.readyState !== WebSocket.OPEN;
    let bg, lines;
    if (offline) {
        bg = VOX_TEAL_DIM; lines = ["PUSH", "TO", "TALK"];
    } else if (voxState.talk === "live") {
        bg = COLOR_RED; lines = ["LIVE"];
    } else if (voxState.talk === "rec") {
        bg = COLOR_RED; lines = ["REC"];
    } else if (voxState.talk === "listen") {
        bg = VOX_TEAL_DIM; lines = ["LISTEN"];
    } else {
        bg = VOX_TEAL; lines = ["PUSH", "TO", "TALK"];
    }
    setAllImages(talkAction, renderSVG(bg, lines));
}

function refreshMode() {
    const offline = !voxWs || voxWs.readyState !== WebSocket.OPEN;
    if (offline) {
        setAllImages(modeAction, renderSVG(VOX_TEAL_DIM, ["MODE"]));
    } else {
        const label = MODE_LABELS[voxState.mode] || "AVAIL";
        const color = MODE_COLORS[voxState.mode] || COLOR_GREEN;
        setAllImages(modeAction, renderSVG(color, [label]));
    }
}

function refreshLogo() {
    const offline = !voxWs || voxWs.readyState !== WebSocket.OPEN;
    if (offline) {
        setAllImages(logoAction, renderVoxLogo());
    } else if (voxState.message && msgPulseOn) {
        setAllImages(logoAction, renderSVG(VOX_TEAL, ["MSG"]));
    } else if (voxState.preview) {
        setAllImages(logoAction, renderSVG(VOX_TEAL, [voxState.preview]));
    } else {
        setAllImages(logoAction, renderVoxLogo());
    }
}

function refreshTeam() {
    const offline = !voxWs || voxWs.readyState !== WebSocket.OPEN;
    if (offline || !voxState.teams || voxState.teams.length === 0) {
        setAllImages(teamAction, renderSVG(VOX_TEAL_DIM, ["TEAM", "--"]));
    } else {
        let team;
        const idx = voxState.browseTeamIndex || 0;
        if (voxState.preview && idx < voxState.teams.length) {
            team = voxState.teams[idx];
        } else {
            team = voxState.teams.find(t => t.id === voxState.activeTeamId) || voxState.teams[0];
        }
        const name = truncName(team.name);
        const isActive = team.id === voxState.activeTeamId;
        const isBrowsing = !!voxState.preview;
        setAllImages(teamAction, renderSVG(
            (isActive || isBrowsing) ? VOX_TEAL : VOX_TEAL_DIM,
            ["TEAM", name]
        ));
    }
}

function refreshUser() {
    const offline = !voxWs || voxWs.readyState !== WebSocket.OPEN;
    if (offline || !voxState.users || voxState.users.length === 0) {
        setAllImages(userAction, renderSVG(VOX_TEAL_DIM, ["USER", "--"]));
    } else {
        let user;
        const idx = voxState.browseUserIndex || 0;
        if (voxState.preview && idx < voxState.users.length) {
            user = voxState.users[idx];
        } else {
            user = voxState.users.find(u => u.id === voxState.activeUserId) || voxState.users[0];
        }
        const name = truncName(user.name);
        const isActive = user.id === voxState.activeUserId;
        const isBrowsing = !!voxState.preview;
        setAllImages(userAction, renderSVG(
            (isActive || isBrowsing) ? VOX_TEAL : VOX_TEAL_DIM,
            ["USER", name]
        ));
    }
}

function refreshPanel() {
    setAllImages(panelAction, renderSVG(VOX_TEAL_DIM, ["MORE"]));
}

function refreshAll() {
    refreshTalk();
    refreshMode();
    refreshLogo();
    refreshTeam();
    refreshUser();
    refreshPanel();
}

function showDisconnected() {
    refreshAll(); // All refresh functions check offline state
}

// ── Message pulse ───────────────────────────────────────────

function startMsgPulse() {
    if (msgPulseTimer) return;
    msgPulseOn = true;
    msgPulseTimer = setInterval(() => {
        msgPulseOn = !msgPulseOn;
        refreshLogo();
    }, 600);
}

function stopMsgPulse() {
    if (msgPulseTimer) { clearInterval(msgPulseTimer); msgPulseTimer = null; }
    msgPulseOn = false;
    refreshLogo();
}

// ── Vox app WebSocket connection ────────────────────────────

function connectToVox() {
    if (voxWs) {
        try { voxWs.close(); } catch (_) {}
    }
    try {
        voxWs = new WebSocket("ws://127.0.0.1:50003");
    } catch (e) {
        logger.error("Vox connection failed: " + e.message);
        scheduleReconnect();
        return;
    }
    voxWs.on("open", () => {
        logger.info("Connected to Vox app");
        reconnectDelay = 3000;
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
        refreshAll();
    });
    voxWs.on("message", (data) => {
        try {
            const msg = JSON.parse(data.toString());
            if (msg.type === "app_quit") {
                logger.info("Vox app quit");
                stopMsgPulse();
                showDisconnected();
                return;
            }
            if (msg.type === "state") {
                const oldMsg = voxState.message;
                Object.assign(voxState, msg);
                delete voxState.type;
                refreshAll();
                if (voxState.message && !oldMsg) startMsgPulse();
                if (!voxState.message && oldMsg) stopMsgPulse();
            }
        } catch (e) {
            logger.error("Vox parse error: " + e.message);
        }
    });
    voxWs.on("close", () => {
        logger.info("Vox disconnected");
        voxWs = null;
        stopMsgPulse();
        scheduleReconnect();
        showDisconnected();
    });
    voxWs.on("error", (e) => {
        logger.error("Vox connection error: " + (e.message || e.code || "unknown"));
        voxWs = null;
        scheduleReconnect();
    });
}

function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectToVox();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
}

function sendToVox(msg) {
    if (voxWs && voxWs.readyState === WebSocket.OPEN) {
        voxWs.send(JSON.stringify(msg));
    }
}

// ── Action classes ──────────────────────────────────────────

class TalkAction extends SingletonAction {
    onWillAppear() { refreshTalk(); }
    onKeyDown() { sendToVox({ action: "ptt_press" }); }
    onKeyUp() { sendToVox({ action: "ptt_release" }); }
}
TalkAction.prototype.manifestId = "com.vox.intercom.talk";

class ModeAction extends SingletonAction {
    onWillAppear() { refreshMode(); }
    onKeyDown() { sendToVox({ action: "cycle_mode" }); }
}
ModeAction.prototype.manifestId = "com.vox.intercom.mode";

class TeamAction extends SingletonAction {
    onWillAppear() { refreshTeam(); }
    onKeyDown() { sendToVox({ action: "cycle_team" }); }
}
TeamAction.prototype.manifestId = "com.vox.intercom.team";

class UserAction extends SingletonAction {
    onWillAppear() { refreshUser(); }
    onKeyDown() { sendToVox({ action: "cycle_user" }); }
}
UserAction.prototype.manifestId = "com.vox.intercom.user";

class LogoAction extends SingletonAction {
    onWillAppear() { refreshLogo(); }
}
LogoAction.prototype.manifestId = "com.vox.intercom.logo";

class PanelAction extends SingletonAction {
    onWillAppear() { refreshPanel(); }
    onKeyDown() { sendToVox({ action: "show_panel" }); }
}
PanelAction.prototype.manifestId = "com.vox.intercom.panel";

// ── Plugin init ─────────────────────────────────────────────

const logger = streamDeck.logger;
logger.setLevel("debug");

talkAction  = new TalkAction();
modeAction  = new ModeAction();
teamAction  = new TeamAction();
userAction  = new UserAction();
logoAction  = new LogoAction();
panelAction = new PanelAction();

streamDeck.actions.registerAction(talkAction);
streamDeck.actions.registerAction(modeAction);
streamDeck.actions.registerAction(teamAction);
streamDeck.actions.registerAction(userAction);
streamDeck.actions.registerAction(logoAction);
streamDeck.actions.registerAction(panelAction);

streamDeck.system.onSystemDidWakeUp(() => {
    logger.info("System woke up — reconnecting to Vox");
    connectToVox();
});

// Connect to Stream Deck, then start Vox bridge
streamDeck.connect().then(() => {
    logger.info("Vox plugin connected to Stream Deck");
    setTimeout(connectToVox, 500);
});
