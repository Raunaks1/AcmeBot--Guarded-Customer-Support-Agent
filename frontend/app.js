"use strict";

const state = { pendingId: null };

const $ = (id) => document.getElementById(id);

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function init() {
  const health = await fetch("/api/health").then((r) => r.json());
  $("llm-badge").textContent = "llm: " + (health.llm || "mock");
}

async function runAgent() {
  const query = $("query").value.trim();
  if (!query) return;
  setBusy(true);
  let data = null;
  try {
    data = await post("/api/run", { query });
  } finally {
    setBusy(false);
  }
  if (!data) return;
  renderResult(data);
  if (data.status === "pending_confirmation") {
    state.pendingId = data.pending_id;
    $("confirm-box").hidden = false;
  } else {
    state.pendingId = null;
    $("confirm-box").hidden = true;
  }
}

async function confirm(approve) {
  if (!state.pendingId) return;
  setBusy(true);
  let data = null;
  try {
    data = await post("/api/confirm", { pending_id: state.pendingId, approve });
  } finally {
    setBusy(false);
  }
  if (!data) return;
  renderResult(data);
  if (data.status !== "pending_confirmation") $("confirm-box").hidden = true;
}

function setBusy(busy) {
  $("run-btn").disabled = busy;
  $("query").disabled = busy;
  $("approve-btn").disabled = busy;
  $("cancel-btn").disabled = busy;
}

const STATUS_LABELS = {
  answered: "Answered",
  blocked: "Blocked",
  no_answer: "No Answer",
  denied: "Denied",
  pending_confirmation: "Pending Confirmation",
  cancelled: "Cancelled",
  error_handled: "Error Handled",
};

function renderResult(data) {
  const badge = $("status-badge");
  badge.textContent = STATUS_LABELS[data.status] || data.status;
  badge.className = "badge status-" + data.status;
  $("answer").textContent = data.answer || "—";

  const citations = $("citations");
  if (data.citations && data.citations.length > 0) {
    citations.classList.remove("hidden");
    citations.innerHTML = "";
    const ul = document.createElement("ul");
    for (const c of data.citations) {
      const li = document.createElement("li");
      li.textContent = c.doc_id + " — \"" + (c.quote || "") + "\"";
      ul.appendChild(li);
    }
    citations.appendChild(ul);
  } else {
    citations.classList.add("hidden");
  }

  const events = $("events");
  if (data.events && data.events.length > 0) {
    events.classList.remove("hidden");
    events.innerHTML = "";
    const ul = document.createElement("ul");
    for (const e of data.events) {
      const li = document.createElement("li");
      const layer = document.createElement("span");
      layer.className = "ev-layer";
      layer.textContent = e.layer;
      const action = document.createElement("span");
      action.className = "ev-action";
      action.textContent = e.action;
      const detail = document.createElement("span");
      detail.className = "ev-detail";
      detail.textContent = e.detail || "";
      li.append(layer);
      li.append(action);
      li.append(detail);
      ul.appendChild(li);
    }
    events.appendChild(ul);
  } else {
    events.classList.add("hidden");
  }

  const metrics = $("metrics");
  if (data.metrics) {
    metrics.classList.remove("hidden");
    metrics.innerHTML = "";
    const items = [
      ["latency_ms", Number(data.metrics.latency_ms).toFixed(1)],
      ["tokens_in", String(data.metrics.input_tokens)],
      ["tokens_out", String(data.metrics.output_tokens)],
      ["llm_calls", String(data.metrics.llm_calls)],
      ["cost_usd", Number(data.metrics.cost_usd).toFixed(6)],
    ];
    for (const [k, v] of items) {
      const div = document.createElement("div");
      div.innerHTML = "<b>" + k + "</b>: " + v;
      metrics.appendChild(div);
    }
  } else {
    metrics.classList.add("hidden");
  }

  $("response").hidden = false;
}

document.addEventListener("DOMContentLoaded", async () => {
  $("run-btn").addEventListener("click", runAgent);
  $("query").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      runAgent();
    }
  });
  $("approve-btn").addEventListener("click", () => confirm(true));
  $("cancel-btn").addEventListener("click", () => confirm(false));
  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      $("query").value = chip.getAttribute("data-query");
      $("query").focus();
    });
  });
  await init();
});
