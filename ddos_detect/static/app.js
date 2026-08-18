/* Dashboard client.
 *
 * Two rules hold throughout:
 *  - No innerHTML with server data. Every value that came from a packet, a
 *    user, or the database is inserted with textContent, so a hostile hostname
 *    or justification string cannot become markup.
 *  - Every state-changing request carries the session's CSRF token.
 */
"use strict";

const state = {
  csrf: null,
  user: null,
  role: "viewer",
  monitors: new Map(),
  scenarios: [],
  stream: null,
};

/* ---------- tiny DOM helpers ---------- */
function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2), value);
      } else node.setAttribute(key, value === true ? "" : String(value));
    }
  }
  for (const child of [].concat(children || [])) {
    if (child === null || child === undefined) continue;
    node.appendChild(typeof child === "object" ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svg(tag, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    node.setAttribute(key, String(value));
  }
  return node;
}

const $ = (id) => document.getElementById(id);
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

/* ---------- formatting ---------- */
function fmtRate(value, unit) {
  const n = Number(value) || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " G" + unit;
  if (n >= 1e6) return (n / 1e6).toFixed(2) + " M" + unit;
  if (n >= 1e3) return (n / 1e3).toFixed(1) + " k" + unit;
  return n.toFixed(n < 10 ? 1 : 0) + " " + unit;
}
const fmtPps = (v) => fmtRate(v, "pkt/s");
const fmtBps = (v) => fmtRate(v, "bit/s");
const fmtTime = (ts) => new Date(Number(ts) * 1000).toLocaleTimeString();
function fmtDate(ts) {
  if (!ts) return "—";
  return new Date(Number(ts) * 1000).toLocaleString();
}

/* ---------- API ---------- */
async function api(path, options) {
  const opts = Object.assign({ method: "GET", credentials: "same-origin" }, options || {});
  opts.headers = Object.assign({ Accept: "application/json" }, opts.headers || {});
  if (opts.body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.headers["X-CSRF-Token"] = state.csrf || "";
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, opts);
  let payload = {};
  try { payload = await response.json(); } catch (_) { /* empty body */ }
  if (!response.ok) {
    const err = new Error(payload.error || `request failed (${response.status})`);
    err.status = response.status;
    throw err;
  }
  return payload;
}

/* ---------- session ---------- */
async function boot() {
  try {
    const session = await api("/api/session");
    enterApp(session);
  } catch (err) {
    showLogin();
  }
}

function showLogin() {
  $("login").classList.remove("hidden");
  $("app").classList.add("hidden");
  $("username").focus();
}

async function enterApp(session) {
  state.csrf = session.csrf_token;
  state.user = session.username;
  state.role = session.role;
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("whoami").textContent = `${session.username} (${session.role})`;
  $("attestation-text").textContent =
    "I confirm I own this address range or have documented permission from its " +
    "operator to monitor traffic to it.";
  if (state.role !== "admin") {
    document.querySelector('[data-panel="audit"]').classList.add("hidden");
  }
  await Promise.all([refreshOverview(), loadScenarios()]);
  connectStream();
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    const session = await api("/api/login", {
      method: "POST",
      body: { username: $("username").value, password: $("password").value },
    });
    $("password").value = "";
    await enterApp(session);
  } catch (err) {
    $("login-error").textContent = err.message;
  }
});

$("logout").addEventListener("click", async () => {
  if (state.stream) state.stream.close();
  try { await api("/api/logout", { method: "POST", body: {} }); } catch (_) { /* ignore */ }
  window.location.reload();
});

/* ---------- tabs ---------- */
$("tabs").addEventListener("click", (event) => {
  const tab = event.target.closest(".tab");
  if (!tab) return;
  for (const button of document.querySelectorAll(".tab")) button.classList.remove("active");
  for (const panel of document.querySelectorAll(".panel")) panel.classList.add("hidden");
  tab.classList.add("active");
  $("panel-" + tab.dataset.panel).classList.remove("hidden");
  if (tab.dataset.panel === "audit") loadAudit();
});

/* ---------- overview ---------- */
async function refreshOverview() {
  const data = await api("/api/overview");
  renderPosture(data.posture);
  state.monitors = new Map(data.monitors.map((m) => [m.id, m]));
  renderMonitors();
  renderAlerts(data.alerts);
  renderAuthorizations(data.authorizations);
}

function renderPosture(posture) {
  const host = $("posture");
  clear(host);
  const badges = [
    [posture.loopback_only ? "loopback only" : "network-exposed", posture.loopback_only],
    [posture.authorization_enforced ? "authorization enforced" : "authorization OFF",
      posture.authorization_enforced],
    [posture.public_targets_allowed ? "public targets allowed" : "private targets only",
      !posture.public_targets_allowed],
    [`headers only (${posture.header_only_snaplen}B)`, true],
    [`retention ${posture.retention_days}d`, true],
    [posture.audit_chain_ok ? "audit chain verified" : "AUDIT CHAIN BROKEN",
      posture.audit_chain_ok],
    [posture.capture && posture.capture.privileged ? "capture ready" : "capture needs privileges",
      !!(posture.capture && posture.capture.privileged)],
  ];
  for (const [text, good] of badges) {
    host.appendChild(el("span", { class: "badge " + (good ? "good" : "warn"), text }));
  }
}

function renderMonitors() {
  const host = $("monitors");
  clear(host);
  const monitors = [...state.monitors.values()];
  $("monitors-empty").classList.toggle("hidden", monitors.length > 0);
  monitors.sort((a, b) => b.created_at - a.created_at);
  for (const monitor of monitors) host.appendChild(monitorCard(monitor));
}

function monitorCard(monitor) {
  const ev = monitor.evaluation || {};
  const metrics = ev.metrics || {};
  const card = el("article", { class: "card monitor" });

  const head = el("div", { class: "monitor-head" }, [
    el("span", { class: "ip", text: monitor.target }),
    monitor.label ? el("span", { class: "muted", text: monitor.label }) : null,
    monitor.simulated ? el("span", { class: "badge warn", text: "simulated" }) : null,
    el("span", { class: "spacer" }),
    el("span", { class: "state " + (ev.state || "learning"), text: ev.state || "starting" }),
    el("button", {
      class: "danger", text: "Stop",
      onclick: () => stopMonitor(monitor.id),
    }),
  ]);
  card.appendChild(head);

  if (ev.state === "learning") {
    card.appendChild(el("p", {
      class: "muted small",
      text: "Learning the traffic baseline. Statistical alerts are held until this completes.",
    }));
  }

  card.appendChild(el("div", { class: "stats" }, [
    stat("packets/s", fmtPps(metrics.pps)),
    stat("bits/s", fmtBps(metrics.bps)),
    stat("SYN/s", fmtPps(metrics.syn_pps)),
    stat("sources", String(metrics.unique_sources || 0)),
    stat("entropy", (metrics.entropy || 0).toFixed(2)),
    stat("score", (ev.score || 0).toFixed(2)),
  ]));

  card.appendChild(chart(monitor.history || []));

  if (ev.signals && ev.signals.length) {
    const signals = el("div", { class: "signals" });
    for (const signal of ev.signals.slice(0, 4)) {
      const bar = el("i");
      bar.style.width = Math.round(signal.score * 100) + "%";
      signals.appendChild(el("div", { class: "signal" }, [
        el("span", { class: "name", text: signal.label }),
        el("span", { class: "meter" }, [bar]),
        el("span", { class: "mono small", text: signal.score.toFixed(2) }),
      ]));
    }
    card.appendChild(signals);
  }

  if (ev.advice && ev.severity && ev.severity !== "none") {
    card.appendChild(el("p", {
      class: "advice",
      text: `${ev.label}: ${ev.advice}`,
    }));
  }

  if (ev.top_sources && ev.top_sources.length) {
    const list = ev.top_sources.slice(0, 5)
      .map((s) => `${s.source} (${s.packets})`).join("  ");
    card.appendChild(el("p", { class: "sources", text: "Top sources: " + list }));
  }
  return card;
}

function stat(key, value) {
  return el("div", { class: "stat" }, [
    el("div", { class: "k", text: key }),
    el("div", { class: "v", text: value }),
  ]);
}

/* A dependency-free sparkline: packet rate as an area, threat score as a line. */
function chart(history) {
  const width = 320;
  const height = 68;
  const node = svg("svg", {
    class: "chart", viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none",
    role: "img", "aria-label": "packet rate and threat score over time",
  });
  const samples = history.slice(-120);
  if (samples.length < 2) {
    node.appendChild(svg("line", {
      x1: 0, y1: height - 1, x2: width, y2: height - 1,
      stroke: "currentColor", "stroke-opacity": 0.2,
    }));
    return node;
  }
  const maxPps = Math.max(1, ...samples.map((s) => s.pps));
  const step = width / (samples.length - 1);
  const y = (value, max) => height - 4 - (value / max) * (height - 10);

  let area = `M 0 ${height}`;
  let line = "";
  samples.forEach((sample, index) => {
    const x = index * step;
    area += ` L ${x.toFixed(1)} ${y(sample.pps, maxPps).toFixed(1)}`;
    line += `${index === 0 ? "M" : "L"} ${x.toFixed(1)} ${y(sample.score, 1).toFixed(1)} `;
  });
  area += ` L ${width} ${height} Z`;

  node.appendChild(svg("path", { d: area, fill: "currentColor", "fill-opacity": 0.12 }));
  node.appendChild(svg("path", {
    d: line, fill: "none", stroke: "currentColor", "stroke-width": 1.5,
    "stroke-opacity": 0.85, "vector-effect": "non-scaling-stroke",
  }));
  const peak = svg("text", { x: 4, y: 12, class: "chart-label" });
  peak.textContent = "peak " + fmtPps(maxPps);
  node.appendChild(peak);
  return node;
}

/* ---------- actions ---------- */
$("monitor-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("monitor-error").textContent = "";
  const protocols = [...document.querySelectorAll(".protocols input:checked")]
    .map((box) => box.value);
  try {
    const monitor = await api("/api/monitors", {
      method: "POST",
      body: { target: $("target").value, label: $("label").value, protocols },
    });
    state.monitors.set(monitor.id, monitor);
    renderMonitors();
    $("target").value = "";
    $("label").value = "";
  } catch (err) {
    $("monitor-error").textContent = err.message;
  }
});

async function stopMonitor(id) {
  try {
    await api("/api/monitors/" + encodeURIComponent(id), { method: "DELETE", body: {} });
  } catch (err) {
    $("monitor-error").textContent = err.message;
  }
  state.monitors.delete(id);
  renderMonitors();
}

async function loadScenarios() {
  try {
    const data = await api("/api/scenarios");
    state.scenarios = data.scenarios;
    const select = $("scenario");
    clear(select);
    for (const scenario of data.scenarios) {
      select.appendChild(el("option", { value: scenario.name, text: scenario.name }));
    }
    describeScenario();
  } catch (_) { /* simulation is optional */ }
}

$("scenario").addEventListener("change", describeScenario);
function describeScenario() {
  const chosen = state.scenarios.find((s) => s.name === $("scenario").value);
  $("scenario-help").textContent = chosen ? chosen.description : "";
}

$("simulate-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("monitor-error").textContent = "";
  try {
    const monitor = await api("/api/simulate", {
      method: "POST",
      body: { scenario: $("scenario").value, speed: Number($("speed").value) },
    });
    state.monitors.set(monitor.id, monitor);
    renderMonitors();
  } catch (err) {
    $("monitor-error").textContent = err.message;
  }
});

/* ---------- alerts ---------- */
function renderAlerts(alerts) {
  const host = $("alerts");
  clear(host);
  if (!alerts.length) {
    host.appendChild(el("p", { class: "muted", text: "No alerts recorded." }));
    return;
  }
  const table = el("table");
  table.appendChild(el("thead", null, [el("tr", null, [
    el("th", { text: "Started" }), el("th", { text: "Target" }), el("th", { text: "Type" }),
    el("th", { text: "Severity" }), el("th", { text: "Peak" }), el("th", { text: "Status" }),
    el("th", { text: "" }),
  ])]));
  const body = el("tbody");
  for (const alert of alerts) {
    const open = !alert.ended_at;
    body.appendChild(el("tr", null, [
      el("td", { class: "mono", text: fmtDate(alert.started_at) }),
      el("td", { class: "mono", text: alert.target }),
      el("td", { text: (alert.evidence && alert.evidence.signals && alert.evidence.signals[0]
        ? alert.evidence.signals[0].label : alert.classification) }),
      el("td", { class: "sev-" + alert.severity, text: alert.severity }),
      el("td", { class: "mono", text: Number(alert.peak_score).toFixed(2) }),
      el("td", { text: open ? "active" : "closed" }),
      el("td", null, [
        alert.acknowledged_by
          ? el("span", { class: "muted small", text: "ack " + alert.acknowledged_by })
          : el("button", {
              class: "ghost", text: "Acknowledge",
              onclick: (e) => acknowledge(alert.id, e.target),
            }),
      ]),
    ]));
  }
  table.appendChild(body);
  host.appendChild(table);
}

async function acknowledge(id, button) {
  button.disabled = true;
  try {
    await api(`/api/alerts/${id}/acknowledge`, { method: "POST", body: {} });
    const data = await api("/api/alerts?limit=50");
    renderAlerts(data.alerts);
  } catch (err) {
    button.disabled = false;
  }
}

/* ---------- authorizations ---------- */
$("authz-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("authz-error").textContent = "";
  try {
    await api("/api/authorizations", {
      method: "POST",
      body: {
        cidr: $("cidr").value,
        justification: $("justification").value,
        days: Number($("days").value),
        attestation: $("attestation").checked,
      },
    });
    $("cidr").value = "";
    $("justification").value = "";
    $("attestation").checked = false;
    const data = await api("/api/authorizations");
    renderAuthorizations(data.authorizations);
  } catch (err) {
    $("authz-error").textContent = err.message;
  }
});

function renderAuthorizations(entries) {
  const host = $("authorizations");
  clear(host);
  if (!entries.length) {
    host.appendChild(el("p", {
      class: "muted",
      text: "No authorized ranges. Record one before starting a monitor.",
    }));
    return;
  }
  const table = el("table");
  table.appendChild(el("thead", null, [el("tr", null, [
    el("th", { text: "Range" }), el("th", { text: "Scope" }), el("th", { text: "Justification" }),
    el("th", { text: "By" }), el("th", { text: "Expires" }), el("th", { text: "" }),
  ])]));
  const body = el("tbody");
  for (const entry of entries) {
    body.appendChild(el("tr", null, [
      el("td", { class: "mono", text: entry.cidr }),
      el("td", { text: entry.scope }),
      el("td", { text: entry.justification }),
      el("td", { text: entry.created_by }),
      el("td", { class: "mono", text: fmtDate(entry.expires_at) }),
      el("td", null, [
        state.role === "admin"
          ? el("button", { class: "danger", text: "Revoke",
                           onclick: () => revoke(entry.id) })
          : null,
      ]),
    ]));
  }
  table.appendChild(body);
  host.appendChild(table);
}

async function revoke(id) {
  try {
    await api("/api/authorizations/" + id, { method: "DELETE", body: {} });
    const data = await api("/api/authorizations");
    renderAuthorizations(data.authorizations);
  } catch (err) {
    $("authz-error").textContent = err.message;
  }
}

/* ---------- audit ---------- */
async function loadAudit() {
  if (state.role !== "admin") return;
  try {
    const data = await api("/api/audit?limit=200");
    $("chain-status").textContent = data.chain_ok
      ? "Chain intact — " + data.chain_detail
      : "CHAIN VERIFICATION FAILED — " + data.chain_detail;
    $("chain-status").className = data.chain_ok ? "muted" : "error";
    const host = $("audit");
    clear(host);
    const table = el("table");
    table.appendChild(el("thead", null, [el("tr", null, [
      el("th", { text: "#" }), el("th", { text: "Time" }), el("th", { text: "Actor" }),
      el("th", { text: "Action" }), el("th", { text: "Detail" }),
    ])]));
    const body = el("tbody");
    for (const record of data.records) {
      body.appendChild(el("tr", null, [
        el("td", { class: "mono", text: record.seq }),
        el("td", { class: "mono", text: fmtDate(record.ts) }),
        el("td", { text: record.actor }),
        el("td", { text: record.action }),
        el("td", { class: "mono small", text: JSON.stringify(record.detail) }),
      ]));
    }
    table.appendChild(body);
    host.appendChild(table);
  } catch (err) {
    $("chain-status").textContent = err.message;
  }
}

/* ---------- live stream ---------- */
function connectStream() {
  if (state.stream) state.stream.close();
  const stream = new EventSource("/api/events", { withCredentials: true });
  state.stream = stream;
  stream.onopen = () => $("stream-dot").classList.add("live");
  stream.onerror = () => {
    $("stream-dot").classList.remove("live");
    // EventSource retries on its own; a persistent failure usually means the
    // session expired, so fall back to a reload after a grace period.
    setTimeout(() => {
      if (stream.readyState === EventSource.CLOSED) boot();
    }, 5000);
  };
  stream.onmessage = (event) => {
    let payload;
    try { payload = JSON.parse(event.data); } catch (_) { return; }
    handleEvent(payload);
  };
}

function handleEvent(event) {
  if (event.type === "metrics") {
    const monitor = state.monitors.get(event.monitor_id);
    if (!monitor) { refreshOverview(); return; }
    monitor.evaluation = event.evaluation;
    monitor.history = (monitor.history || []).concat([event.sample]).slice(-180);
    renderMonitors();
  } else if (event.type === "alert") {
    api("/api/alerts?limit=50").then((data) => renderAlerts(data.alerts)).catch(() => {});
  } else if (event.type === "monitor") {
    refreshOverview();
  }
}

boot();
