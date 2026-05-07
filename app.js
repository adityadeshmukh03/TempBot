const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Request failed");
  }
  return data;
}

function money(value) {
  const num = Number(value || 0);
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(num);
}

function table(el, rows, columns) {
  if (!rows || rows.length === 0) {
    el.innerHTML = "<tbody><tr><td>No data yet</td></tr></tbody>";
    return;
  }
  const head = columns.map((c) => `<th>${c.label}</th>`).join("");
  const body = rows
    .slice()
    .reverse()
    .map((row) => {
      const cells = columns.map((c) => `<td>${row[c.key] || ""}</td>`).join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  el.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
}

function renderChecklist(config) {
  const items = [
    ["Kite key", config.kite_key_set],
    ["Gemini key", config.gemini_key_set],
    ["Access token", config.access_token_exists],
    ["State database", config.state_db_exists],
    ["Broker SL", config.broker_sl_enabled],
    ["EOD exit", config.auto_eod_exit],
  ];
  $("checklist").innerHTML = items
    .map(([label, ok]) => `<li><span>${label}</span><strong class="${ok ? "ok" : "no"}">${ok ? "Ready" : "Missing"}</strong></li>`)
    .join("");
}

function renderFlow(flow) {
  $("flowBox").innerHTML = (flow || [])
    .map((item) => `
      <div class="flow-item">
        <b>${item.step}. ${item.name}</b>
        <span>${item.detail}</span>
      </div>
    `)
    .join("");
}

function render(data) {
  const running = data.process.running;
  $("botStatus").textContent = running ? `Running ${data.process.pid}` : "Stopped";
  $("botStatus").className = `status-pill ${running ? "running" : "stopped"}`;
  $("startPaper").disabled = running;
  $("startLive").disabled = running;
  $("stopBot").disabled = !running;

  const pos = data.open_position || {};
  const risk = data.daily_risk || {};
  const cfg = data.config || {};
  $("modeTitle").textContent = running ? "Bot Is Running" : "Paper Mode Ready";
  $("modeSubtitle").textContent = running ? "Watch position, logs, and signals here." : "Start safely in paper mode, then unlock live only when ready.";
  $("positionText").textContent = pos.in_position ? `${pos.symbol || "Open"} ${pos.units || 0}` : "Flat";
  $("pnlText").textContent = money(risk.daily_pnl || 0);
  $("instrumentText").textContent = cfg.instrument || "NIFTY";
  $("safetyText").textContent = risk.circuit_open ? "Circuit Open" : "OK";
  $("positionBox").textContent = JSON.stringify(pos, null, 2);

  renderChecklist(cfg);
  renderFlow(data.model_flow);
  table($("signalsTable"), data.recent_signals, [
    { key: "time", label: "Time" },
    { key: "symbol", label: "Symbol" },
    { key: "signal", label: "Signal" },
    { key: "audit_verdict", label: "Audit" },
    { key: "confidence", label: "Confidence" },
  ]);
  table($("tradesTable"), data.recent_trades, [
    { key: "time", label: "Time" },
    { key: "direction", label: "Side" },
    { key: "signal", label: "Signal" },
    { key: "entry", label: "Entry" },
    { key: "stop_loss", label: "SL" },
  ]);
}

async function refresh() {
  try {
    const data = await api("/api/status");
    render(data);
  } catch (err) {
    $("botStatus").textContent = "API Error";
    $("botStatus").className = "status-pill stopped";
  }
}

async function refreshLogs() {
  const name = $("logSelect").value;
  try {
    const data = await api(`/api/logs?name=${encodeURIComponent(name)}&lines=160`);
    $("logBox").textContent = data.lines.join("\n") || "No log lines yet";
  } catch (err) {
    $("logBox").textContent = err.message;
  }
}

$("startPaper").addEventListener("click", async () => {
  await api("/api/bot/start", {
    method: "POST",
    body: JSON.stringify({ live_mode: false, wait_for_market_open: true }),
  });
  await refresh();
});

$("startLive").addEventListener("click", async () => {
  await api("/api/bot/start", {
    method: "POST",
    body: JSON.stringify({
      live_mode: true,
      confirm: $("liveConfirm").value,
      wait_for_market_open: true,
    }),
  });
  $("liveConfirm").value = "";
  await refresh();
});

$("stopBot").addEventListener("click", async () => {
  await api("/api/bot/stop", { method: "POST", body: "{}" });
  await refresh();
});

$("logSelect").addEventListener("change", refreshLogs);

refresh();
refreshLogs();
setInterval(refresh, 3000);
setInterval(refreshLogs, 5000);
