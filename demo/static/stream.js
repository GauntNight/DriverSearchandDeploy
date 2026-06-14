/* Batch-stream page: a live, independently-actionable card per queued package.
 *
 * Subscribes to ONE fan-in SSE (/api/demo/stream/batch/{batch_id}); every
 * envelope is tagged with its job_id, so we route it to the right card. Unlike
 * the main console there is NO global single-action lock — each card surfaces
 * and resolves its own gate / URL-confirm / installer-drop, so a batch of N
 * packages never makes the operator wait on one keyhole. */
(function () {
  "use strict";

  const STAGES = ["pending", "discovering", "packaging", "testing", "deploying"];
  const TERMINAL = new Set(["completed", "failed", "cancelled"]);
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const batchId = params.get("batch") || params.get("batch_id") || "";

  const cards = new Map();   // job_id -> { el, state }
  let evtSource = null;

  function setCounts() {
    const n = cards.size;
    let done = 0, fail = 0;
    cards.forEach((c) => { if (c.state === "completed") done++; else if (c.state === "failed") fail++; });
    $("counts").textContent =
      `${n} package${n === 1 ? "" : "s"}` +
      (done ? ` · ${done} done` : "") + (fail ? ` · ${fail} failed` : "");
  }

  function ensureCard(jobId, name) {
    if (cards.has(jobId)) {
      if (name) cards.get(jobId).el.querySelector(".card-name").textContent = name;
      return cards.get(jobId);
    }
    const empty = $("empty"); if (empty) empty.remove();
    const tpl = $("card-tpl").content.cloneNode(true);
    const el = tpl.querySelector(".card");
    el.dataset.job = String(jobId);
    el.querySelector(".card-name").textContent = name || `job #${jobId}`;
    el.querySelector(".card-job").textContent = `job #${jobId}`;
    $("grid").appendChild(el);
    const card = { el, state: "pending" };
    cards.set(jobId, card);
    setCounts();
    return card;
  }

  function setLamp(card, lampState, text) {
    const lamp = card.el.querySelector(".lamp");
    if (lamp && lampState) lamp.dataset.state = lampState;
    if (text) card.el.querySelector(".card-line").textContent = text;
  }

  function advanceStepper(card, state) {
    if (!state) return;
    const lis = card.el.querySelectorAll(".mini-stepper li");
    if (state === "failed") {
      lis.forEach((li) => { if (li.classList.contains("active")) { li.classList.remove("active"); li.classList.add("failed"); } });
      return;
    }
    if (state === "completed") {
      lis.forEach((li) => { li.classList.remove("active"); li.classList.add("done"); });
      return;
    }
    const idx = STAGES.indexOf(state);
    if (idx < 0) return;
    lis.forEach((li) => {
      const li_idx = STAGES.indexOf(li.dataset.step);
      li.classList.remove("active", "done", "failed");
      if (li_idx < idx) li.classList.add("done");
      else if (li_idx === idx) li.classList.add("active");
    });
  }

  function setBadge(card, state) {
    const badge = card.el.querySelector(".card-badge");
    const map = {
      running: "running", gate: "needs approval", awaiting: "needs installer",
      completed: "deployed", failed: "failed",
    };
    badge.dataset.state = state;
    badge.textContent = map[state] || state;
    card.el.dataset.flash =
      state === "gate" ? "gate" : state === "completed" ? "done" : state === "failed" ? "failed" : "";
  }

  function clearAction(card) { card.el.querySelector(".card-action").innerHTML = ""; }

  function setState(card, state) {
    card.state = state;
    if (STAGES.includes(state)) setBadge(card, "running");
    else setBadge(card, state);
    setCounts();
  }

  // ---- Inline per-card actions ---------------------------------------------
  function renderApprove(card, jobId) {
    setState(card, "gate");
    const box = card.el.querySelector(".card-action");
    box.innerHTML = "";
    const btn = document.createElement("button");
    btn.className = "btn approve"; btn.textContent = "Approve & deploy";
    btn.addEventListener("click", async () => {
      // Clicking Approve IS the decision to deploy — no extra confirm pop-up.
      // (A richer approval gate / approval screen is a roadmap item.)
      btn.disabled = true; btn.textContent = "Approving…";
      try {
        await fetch(`/api/demo/jobs/${jobId}/approve`, { method: "POST" });
        clearAction(card); setState(card, "deploying"); advanceStepper(card, "deploying");
      } catch (e) { btn.disabled = false; btn.textContent = "Approve & deploy"; }
    });
    box.appendChild(btn);
  }

  function renderConfirmUrl(card, jobId, url, provenance, confidence) {
    setState(card, "awaiting");
    const box = card.el.querySelector(".card-action");
    box.innerHTML = "";
    if (url) {
      const u = document.createElement("div"); u.className = "url"; u.textContent = url; box.appendChild(u);
    }
    if (provenance || confidence) {
      const p = document.createElement("div"); p.className = "prov";
      p.textContent = `source: ${provenance || "web search"}${confidence ? ` · confidence: ${confidence}` : ""}`;
      box.appendChild(p);
    }
    const row = document.createElement("div"); row.className = "row";
    const ok = document.createElement("button"); ok.className = "btn small"; ok.textContent = "Confirm source";
    const no = document.createElement("button"); no.className = "btn bad small"; no.textContent = "Reject";
    ok.addEventListener("click", async () => {
      ok.disabled = no.disabled = true; ok.textContent = "Fetching…";
      try {
        await fetch(`/api/demo/queue/${jobId}/confirm-url`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(url ? { url } : {}),
        });
        clearAction(card); setState(card, "discovering"); advanceStepper(card, "discovering");
      } catch (e) { ok.disabled = no.disabled = false; ok.textContent = "Confirm source"; }
    });
    no.addEventListener("click", async () => {
      ok.disabled = no.disabled = true;
      try { await fetch(`/api/demo/jobs/${jobId}/cancel`, { method: "POST" }); } catch (e) { /* ignore */ }
      clearAction(card); setState(card, "failed"); advanceStepper(card, "failed");
      card.el.querySelector(".card-line").textContent = "Rejected source — cancelled.";
    });
    row.appendChild(ok); row.appendChild(no); box.appendChild(row);
  }

  function markFailed(card, jobId) {
    advanceStepper(card, "failed");
    setState(card, "failed");
    renderFailedActions(card, jobId);
  }

  function renderFailedActions(card, jobId) {
    const box = card.el.querySelector(".card-action");
    box.innerHTML = "";
    const row = document.createElement("div"); row.className = "fail-row";
    const retry = document.createElement("button");
    retry.className = "btn retry small"; retry.textContent = "↻ Retry";
    retry.title = "Re-run this package's pipeline";
    retry.addEventListener("click", async () => {
      retry.disabled = true; retry.textContent = "Retrying…";
      try {
        await fetch(`/api/demo/jobs/${jobId}/retry`, { method: "POST" });
        clearAction(card);
        card.el.querySelector(".card-line").classList.remove("error");
        card.el.querySelectorAll(".mini-stepper li").forEach((li) => li.classList.remove("done", "active", "failed"));
        advanceStepper(card, "pending"); setState(card, "pending");
        card.el.querySelector(".card-line").textContent = "↻ Retry requested — re-running…";
        reconnect();  // the batch stream may have closed once all items settled
      } catch (e) { retry.disabled = false; retry.textContent = "↻ Retry"; }
    });
    const logs = document.createElement("button");
    logs.className = "btn ghost small"; logs.textContent = "View logs";
    logs.title = "Inspect this execution's diagnostic log";
    logs.addEventListener("click", () => showLogs(jobId, card));
    row.appendChild(retry); row.appendChild(logs);
    box.appendChild(row);
  }

  async function showLogs(jobId, card) {
    const modal = $("logs-modal");
    const pre = $("logs-pre");
    const title = $("logs-title");
    const name = card ? card.el.querySelector(".card-name").textContent : `job #${jobId}`;
    title.textContent = `Logs — ${name} (job #${jobId})`;
    pre.textContent = "Loading…";
    modal.classList.remove("hidden");
    try {
      const r = await fetch(`/api/demo/jobs/${jobId}/logs`);
      const data = await r.json();
      pre.textContent = data.logs || data.error || "(no logs)";
    } catch (e) { pre.textContent = `Failed to load logs: ${e}`; }
  }

  function reconnect() {
    if (evtSource) { try { evtSource.close(); } catch (e) { /* ignore */ } }
    connect();
  }

  function renderInstallerDrop(card, jobId) {
    setState(card, "awaiting");
    const box = card.el.querySelector(".card-action");
    box.innerHTML = "";
    const hint = document.createElement("div"); hint.className = "prov";
    hint.textContent = "No source found automatically — drop the installer (.msi/.exe/.zip).";
    const file = document.createElement("input"); file.type = "file"; file.accept = ".msi,.exe,.zip";
    file.addEventListener("change", async () => {
      if (!file.files || !file.files[0]) return;
      const fd = new FormData(); fd.append("file", file.files[0]);
      file.disabled = true;
      try {
        await fetch(`/api/demo/queue/${jobId}/installer`, { method: "POST", body: fd });
        clearAction(card); setState(card, "packaging"); advanceStepper(card, "packaging");
      } catch (e) { file.disabled = false; }
    });
    box.appendChild(hint); box.appendChild(file);
  }

  // ---- Event routing --------------------------------------------------------
  function handle(env) {
    if (env == null) return;
    if (env.type === "hello") return;
    const jobId = env.job_id;
    if (jobId == null) return;
    const card = ensureCard(jobId);

    switch (env.type) {
      case "lamp":
        setLamp(card, env.lamp, env.text);
        break;
      case "state":
        if (env.state && !TERMINAL.has(card.state)) { advanceStepper(card, env.state); setState(card, env.state); }
        if (env.state === "completed") { advanceStepper(card, "completed"); setState(card, "completed"); clearAction(card); }
        if (env.state === "failed") markFailed(card, jobId);
        break;
      case "console":
        if (env.text) {
          const line = card.el.querySelector(".card-line");
          line.textContent = env.text;
          line.classList.toggle("error", env.level === "error");
        }
        if (env.state && !TERMINAL.has(card.state)) { advanceStepper(card, env.state); setState(card, env.state); }
        if (env.awaiting_confirm === true) renderConfirmUrl(card, jobId, env.proposed_url, env.provenance, env.confidence);
        else if (env.awaiting_installer === true) renderInstallerDrop(card, jobId);
        else if (env.gate === true) renderApprove(card, jobId);
        else if (env.state === "completed") { setState(card, "completed"); clearAction(card); }
        else if (env.state === "failed") markFailed(card, jobId);
        break;
      case "end":
        // Stream closed for this job; leave its last rendered state intact.
        break;
      default: break;
    }
  }

  // ---- Bootstrap ------------------------------------------------------------
  async function loadSnapshot() {
    try {
      const r = await fetch(`/api/demo/queue/${batchId}/snapshot`);
      const data = await r.json();
      (data.jobs || []).forEach((j) => {
        const card = ensureCard(j.job_id, j.name);
        if (j.state) { advanceStepper(card, j.state); setState(card, j.state); }
        // Reseed a PARKED action from the persisted demo sub-state, so opening
        // the page late (or refreshing) still shows the drop/confirm prompt that
        // arrived only as a one-shot live SSE event.
        if (j.state === "completed") { advanceStepper(card, "completed"); setState(card, "completed"); }
        else if (j.state === "failed") markFailed(card, j.job_id);
        else if (j.origin_state === "awaiting_installer") renderInstallerDrop(card, j.job_id);
        else if (j.origin_state === "awaiting_confirm") renderConfirmUrl(card, j.job_id, j.proposed_url, null, null);
      });
    } catch (e) { /* SSE seed will still populate */ }
  }

  function connect() {
    evtSource = new EventSource(`/api/demo/stream/batch/${batchId}`);
    evtSource.onmessage = (e) => {
      let env; try { env = JSON.parse(e.data); } catch { return; }
      handle(env);
    };
    evtSource.onerror = () => { /* browser auto-reconnects */ };
  }

  function wireHeader() {
    $("batch-id").textContent = batchId ? `#${batchId}` : "";
    const modal = $("logs-modal");
    $("logs-close").addEventListener("click", () => modal.classList.add("hidden"));
    modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.add("hidden"); });
    $("cancel-batch").addEventListener("click", async () => {
      const ids = Array.from(cards.keys());
      if (!ids.length) return;
      try {
        await fetch("/api/demo/queue/cancel", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job_ids: ids }),
        });
      } catch (e) { /* ignore */ }
    });
  }

  if (!batchId) {
    wireHeader();
    return;
  }
  wireHeader();
  loadSnapshot().then(connect);
})();
