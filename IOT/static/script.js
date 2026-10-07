const POLL_INTERVAL_MS = 2000;
const SESSION_TIMEOUT_MINUTES = Number(document.body.dataset.sessionTimeout || 5);
const SESSION_TIMEOUT_MS = SESSION_TIMEOUT_MINUTES * 60 * 1000;
let activitySinceLastPoll = false;
let sessionExpired = false;
let idleTimer = null;

const $ = (id) => document.getElementById(id);

function text(id, value) {
    $(id).textContent = value === null || value === undefined || value === "" ? "--" : value;
}

function applyDot(id, ok, known = true) {
    const element = $(id);
    element.classList.remove("ok", "warn", "bad");

    if (!known) {
        element.classList.add("warn");
        return;
    }

    element.classList.add(ok ? "ok" : "bad");
}

function formatPir(value) {
    if (value === 1 || value === "1") {
        return "Motion";
    }

    if (value === 0 || value === "0") {
        return "Clear";
    }

    return "--";
}

function formatDistance(value) {
    if (value === null || value === undefined || value === "") {
        return "--";
    }

    const numeric = Number(value);
    return Number.isFinite(numeric) ? `${numeric.toFixed(1)} cm` : String(value);
}

function formatYolo(value) {
    if (!value) {
        return "--";
    }

    return String(value).toUpperCase();
}

function setThreatClass(level) {
    const element = $("threatLevel");
    element.classList.remove("threat-low", "threat-medium", "threat-high", "threat-unknown");

    const normalized = String(level || "UNKNOWN").toLowerCase();
    if (normalized === "low") {
        element.classList.add("threat-low");
    } else if (normalized === "medium") {
        element.classList.add("threat-medium");
    } else if (normalized === "high") {
        element.classList.add("threat-high");
    } else {
        element.classList.add("threat-unknown");
    }
}

function threatChip(level) {
    const normalized = String(level || "UNKNOWN").toLowerCase();
    const className = normalized === "low"
        ? "threat-low"
        : normalized === "medium"
            ? "threat-medium"
            : normalized === "high"
                ? "threat-high"
                : "threat-unknown";

    return `<span class="threat-chip ${className}">${escapeHtml(level || "UNKNOWN")}</span>`;
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

async function getJson(url, headers = {}) {
    const response = await fetch(url, {
        cache: "no-store",
        credentials: "same-origin",
        headers,
    });

    if (response.redirected) {
        window.location.href = response.url;
        throw new Error("Authentication required");
    }

    const contentType = response.headers.get("content-type") || "";

    if (!response.ok) {
        throw new Error(`${url} returned ${response.status}`);
    }

    if (!contentType.includes("application/json")) {
        window.location.href = "/login";
        throw new Error("Authentication required");
    }

    return response.json();
}

function updateStatus(data) {
    const decision = data.decision || {};
    const sensors = data.sensors || {};
    const health = data.health || {};

    text("liveTimestamp", data.timestamp);
    text("systemState", data.system_state);
    text("threatLevel", decision.threat || "UNKNOWN");
    text("decisionMessage", decision.message || "Awaiting backend decision");
    text("fusionDecision", decision.threat ? `${decision.threat}: ${decision.message}` : "--");
    text("fusionSummary", decision.threat || "--");
    setThreatClass(decision.threat);

    text("pirStatus", formatPir(sensors.pir));
    text("distanceValue", formatDistance(sensors.distance));
    text("yoloDetection", formatYolo(sensors.yolo));

    const mqtt = health.mqtt || {};
    text("mqttStatus", `MQTT ${mqtt.state || "--"}`);
    text("healthMqtt", mqtt.state);
    applyDot("mqttDot", Boolean(mqtt.ok), Boolean(mqtt.state));
    applyDot("healthMqttDot", Boolean(mqtt.ok), Boolean(mqtt.state));

    updateHealth("healthPi", "healthPiDot", health.raspberry_pi);
    updateHealth("healthCamera", "healthCameraDot", health.camera);
    updateHealth("healthYolo", "healthYoloDot", health.yolo);
}

function updateHealth(labelId, dotId, item) {
    const health = item || {};
    text(labelId, health.state);
    applyDot(dotId, Boolean(health.ok), Boolean(health.state));
}

function updateAlerts(data) {
    const alerts = data.alerts || [];
    const events = data.events || [];
    const table = $("alertsTable");

    if (!alerts.length) {
        table.innerHTML = '<tr><td colspan="6">Awaiting backend alerts</td></tr>';
    } else {
        table.innerHTML = alerts.slice(0, 50).map((alert) => `
            <tr>
                <td>${escapeHtml(alert.time)}</td>
                <td>${escapeHtml(alert.pir)}</td>
                <td>${escapeHtml(alert.distance)}</td>
                <td>${escapeHtml(alert.yolo)}</td>
                <td>${threatChip(alert.threat)}</td>
                <td>${escapeHtml(alert.message)}</td>
            </tr>
        `).join("");
    }

    const log = $("eventLog");
    if (!events.length) {
        log.innerHTML = '<div class="empty-state">Awaiting backend events</div>';
    } else {
        log.innerHTML = events.slice(0, 80).map((event) => `
            <div class="log-entry">
                <strong>${escapeHtml(event.source)} / ${escapeHtml(event.kind)}</strong>
                ${escapeHtml(event.message)}
                <span>${escapeHtml(event.time)}</span>
            </div>
        `).join("");
    }
}

function updateStatistics(data) {
    text("totalDetections", data.total_detections);
    text("falseAlarms", data.false_alarms);
    text("systemUptime", data.system_uptime);
    text("detectionCount", data.detection_count);
}

async function refreshDashboard() {
    if (sessionExpired) {
        return;
    }

    try {
        const headers = activitySinceLastPoll ? { "X-User-Activity": "1" } : {};
        activitySinceLastPoll = false;

        const [status, alerts, statistics] = await Promise.all([
            getJson("/status", headers),
            getJson("/alerts", headers),
            getJson("/statistics", headers),
        ]);

        updateStatus(status);
        updateAlerts(alerts);
        updateStatistics(statistics);
    } catch (error) {
        text("systemState", "API ERROR");
        text("decisionMessage", error.message);
        setThreatClass("UNKNOWN");
    }
}

function showSessionTimeoutOverlay() {
    const overlay = document.createElement("div");
    overlay.className = "session-timeout-overlay is-visible";
    overlay.innerHTML = `
        <div class="session-timeout-card">
            <strong>Session expired</strong>
            <p>You were logged out after ${SESSION_TIMEOUT_MINUTES} minutes of inactivity.</p>
        </div>
    `;
    document.body.appendChild(overlay);
}

async function expireSession() {
    if (sessionExpired) {
        return;
    }

    sessionExpired = true;
    showSessionTimeoutOverlay();

    try {
        await fetch("/logout?reason=timeout", {
            method: "POST",
            credentials: "same-origin",
            cache: "no-store",
        });
    } catch (error) {
        // The redirect below still takes the user to a clean login state.
    }

    window.location.href = "/login?reason=timeout";
}

function resetIdleTimer() {
    if (sessionExpired) {
        return;
    }

    activitySinceLastPoll = true;
    window.clearTimeout(idleTimer);
    idleTimer = window.setTimeout(expireSession, SESSION_TIMEOUT_MS);
}

["click", "keydown", "pointerdown", "touchstart", "wheel"].forEach((eventName) => {
    window.addEventListener(eventName, () => {
        resetIdleTimer();
    }, { passive: true });
});

document.querySelectorAll("[data-export]").forEach((button) => {
    button.addEventListener("click", () => {
        window.location.href = button.dataset.export;
    });
});

resetIdleTimer();
activitySinceLastPoll = false;
refreshDashboard();
setInterval(refreshDashboard, POLL_INTERVAL_MS);
