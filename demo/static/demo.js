/* AutoPackager — Mission Control demo console.
   One SSE stream per job multiplexes pipeline + Claude lines; the AI lamp and
   left stepper are styled consumers of the same stream. Center panel polls the
   real tenant (or fixtures) independently. */

(() => {
  "use strict";

  const STEPS = ["pending", "discovering", "packaging", "testing", "deploying", "completed"];
  const $ = (id) => document.getElementById(id);

  // Only these installer types are ever processed. Guards against misclicks and
  // junk files (drop, file-picker, and the URL field all funnel through here).
  const ALLOWED_EXT = [".msi", ".exe", ".zip"];
  function extOf(name) {
    const base = (name || "").toLowerCase().split("?")[0].split("#")[0];
    const m = base.match(/\.[a-z0-9]+$/);
    return m ? m[0] : "";
  }
  function isAllowedName(name) {
    return ALLOWED_EXT.includes(extOf(name));
  }

  let currentJob = null;
  let evtSource = null;
  let gateMode = false;
  let lastAppId = null;
  const seenAppIds = new Set();
  let firstIntuneLoad = true;

  // Supersedence demo state.
  let lastView = null;                 // cache the last Intune view for re-renders
  const appMeta = new Map();           // app_id -> { entry_id, latest_version, download_url, current_version }
  const badgeOverride = new Map();     // app_id -> { state, label } (client-driven, e.g. after a check)
  let pendingUpgrade = null;           // { app_id, scope } awaiting a manual installer drop
  let scopeApp = null;                 // app the open scope dialog targets

  // ---- Single-action lock + queue batch state ------------------------------
  // The console runs ONE action at a time. While an action is in flight, other
  // actions are greyed out (body.busy) and a Cancel button shows in the right
  // panel. An action "settles" when its automated work is done — it reached the
  // Ring 0 approval gate, completed, failed, or was cancelled.
  let busy = false;
  let currentSettled = false;          // guard: settle each streamed job once
  let activeBatch = null;              // { jobs:[{job_id,name}], idx, job_ids } | null
  let pendingQueueInstaller = null;    // job_id of an awaiting-installer queue item
  let pendingConfirm = null;           // { job_id, url } awaiting URL confirm

  function setBusy(on, status) {
    busy = on;
    document.body.classList.toggle("busy", on);
    const bar = $("action-bar");
    if (bar) bar.classList.toggle("hidden", !on);
    if (on && status) $("action-status").textContent = status;
  }

  // ---- Preflight -----------------------------------------------------------
  async function preflight() {
    try {
      const r = await fetch("/api/demo/preflight");
      const p = await r.json();
      setLight("light-ai", p.ai && p.ai.ok, p.ai && p.ai.detail);
      setLight("light-redis", p.redis && p.redis.ok, p.redis && p.redis.detail);
      setLight("light-graph", p.graph && p.graph.ok ? true : "warn",
               p.graph && p.graph.detail);
      setLight("light-worker", p.worker && p.worker.ok, p.worker && p.worker.detail);
      const mode = (p.ai && p.ai.mode) || "?";
      $("mode-badge").textContent = "mode: " + mode;
      setLamp(p.lamp || "offline",
              p.ai && p.ai.ok ? "authenticated · standing by" : (p.ai && p.ai.detail) || "");
    } catch (e) {
      setLight("light-redis", false, String(e));
    }
  }

  function setLight(id, ok, title) {
    const el = $(id);
    if (!el) return;
    el.dataset.ok = ok === "warn" ? "warn" : (ok ? "true" : "false");
    if (title) el.title = title;
  }

  // ---- Lamp ----------------------------------------------------------------
  const LAMP_LABELS = {
    offline:  ["Offline", "preflight not run"],
    checking: ["Checking…", "running health check"],
    ready:    ["Ready", "authenticated · standing by"],
    thinking: ["Thinking", "researching package…"],
    error:    ["Auth failed", "see console"],
  };
  function setLamp(state, sub) {
    const lamp = $("lamp");
    if (!lamp) return;
    lamp.dataset.state = state;
    const [label, defSub] = LAMP_LABELS[state] || ["—", ""];
    $("lamp-label").textContent = label;
    $("lamp-sub").textContent = sub || defSub;
  }

  // ---- Stepper -------------------------------------------------------------
  function resetStepper() {
    document.querySelectorAll(".stepper li").forEach((li) => {
      li.classList.remove("active", "done", "failed");
    });
  }
  function advanceStepper(state) {
    if (!STEPS.includes(state) && state !== "failed") return;
    if (state === "failed") {
      const active = document.querySelector(".stepper li.active");
      if (active) { active.classList.remove("active"); active.classList.add("failed"); }
      return;
    }
    const idx = STEPS.indexOf(state);
    document.querySelectorAll(".stepper li").forEach((li) => {
      const li_idx = STEPS.indexOf(li.dataset.state);
      li.classList.remove("active", "done", "failed");
      if (li_idx < idx) li.classList.add("done");
      else if (li_idx === idx) {
        li.classList.add(state === "completed" ? "done" : "active");
      }
    });
    if (state === "completed") {
      document.querySelectorAll(".stepper li").forEach((li) => li.classList.add("done"));
    }
  }

  // ---- Console -------------------------------------------------------------
  // Source display labels. The research bridge's internal source is "claude"
  // but the customer-facing brand is AutoPackager — never surface "Claude".
  const SRC_LABELS = { claude: "AutoPackager", pipeline: "Pipeline", system: "System" };

  function appendLine(env) {
    const con = $("console");
    const line = document.createElement("div");
    line.className = "cline " + (env.level || "info") +
      (env.source === "claude" ? " claude" : "");
    const ts = (env.ts || "").slice(11, 19) || nowHHMMSS();
    const src = env.source || "system";
    const label = SRC_LABELS[src] || src;
    line.innerHTML =
      `<span class="ts">${ts}</span>` +
      `<span class="src ${src}">${label}</span>` +
      `<span class="msg"></span>`;
    line.querySelector(".msg").textContent = env.text || "";
    con.appendChild(line);
    con.scrollTop = con.scrollHeight;
  }
  function nowHHMMSS() { return new Date().toTimeString().slice(0, 8); }

  // ---- SSE stream ----------------------------------------------------------
  function openStream(jobId, appName) {
    if (evtSource) { evtSource.close(); evtSource = null; }
    currentJob = jobId;
    currentSettled = false;
    pendingQueueInstaller = null;
    resetStepper();
    advanceStepper("pending");
    $("console").innerHTML = "";
    $("job-head").querySelector(".job-id").textContent = "job #" + jobId;
    if (appName) $("job-app").textContent = appName;
    $("gate-box").classList.add("hidden");
    $("escalation-box").classList.add("hidden");
    // Engage the single-action lock (a batch keeps it engaged across items).
    setBusy(true, activeBatch
      ? `Queue: ${activeBatch.idx + 1}/${activeBatch.jobs.length} — ${appName || "item"}`
      : (appName ? `Working — ${appName}` : "Working…"));

    evtSource = new EventSource(`/api/demo/stream/${jobId}`);
    evtSource.onmessage = (e) => {
      let env;
      try { env = JSON.parse(e.data); } catch { return; }
      handleEvent(env);
    };
    evtSource.onerror = () => { /* browser auto-reconnects; keep quiet */ };
  }

  function handleEvent(env) {
    switch (env.type) {
      case "lamp":
        setLamp(env.lamp, env.text);
        break;
      case "state":
        if (env.state) advanceStepper(env.state);
        if (env.state === "completed") settleCurrent("completed");
        else if (env.state === "failed") settleCurrent("failed");
        break;
      case "console":
        appendLine(env);
        if (env.state) advanceStepper(env.state);
        captureSideEffects(env);
        // Settle the action when its automated work is done: it reached the
        // approval gate, completed, failed, or parked awaiting an installer.
        if (env.awaiting_confirm === true) {
          showConfirmBox(currentJob, env.proposed_url, env.provenance, env.confidence);
          settleCurrent("confirm");
        } else if (env.awaiting_installer === true) {
          pendingQueueInstaller = currentJob;
          $("dropzone").classList.add("drag");  // prompt the manual installer drop
          settleCurrent("awaiting");
        } else if (env.gate === true) settleCurrent("gate");
        else if (env.state === "completed") settleCurrent("completed");
        else if (env.state === "failed") settleCurrent("failed");
        break;
      case "end":
        settleCurrent("end");
        break;
      case "hello":
      default:
        break;
    }
  }

  // An action settles exactly once per streamed job. For a batch this advances
  // to the next item; for a single action it releases the lock. On the gate
  // (single action) we keep the stream open so a later Approve still narrates.
  function settleCurrent(reason) {
    if (currentSettled) return;
    currentSettled = true;
    pollIntune(); // refresh the center panel — the payoff shot

    if (activeBatch) {
      if (evtSource) { evtSource.close(); evtSource = null; }
      advanceBatch();
      return;
    }
    if (reason === "end" || reason === "failed") {
      if (evtSource) { evtSource.close(); evtSource = null; }
    }
    setBusy(false);
  }

  function advanceBatch() {
    if (!activeBatch) return;
    const next = activeBatch.idx + 1;
    if (next >= activeBatch.jobs.length) { finishBatch(); return; }
    streamBatchItem(next);
  }

  function finishBatch() {
    const n = activeBatch ? activeBatch.jobs.length : 0;
    activeBatch = null;
    setBusy(false);
    if (n) appendLine({ source: "system", text:
      `Queue batch finished — ${n} item(s) processed. Approve any gated jobs to deploy to Ring 0.` });
    pollIntune();
  }

  function captureSideEffects(env) {
    const text = env.text || "";
    // Reveal the approval gate when testing passes in gate mode.
    if (gateMode && env.gate === true) {
      $("gate-box").classList.remove("hidden");
    }
    // Engineer-escalation: a non-silent installer couldn't be packaged after
    // the retry ladder — surface a prominent banner for manual review.
    if (env.escalation === true) {
      $("esc-msg").textContent = text || "No silent install succeeded — manual review required.";
      $("escalation-box").classList.remove("hidden");
    }
    // Capture the published Intune app id for the Verify deep-link.
    const m = text.match(/app\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
    if (m) lastAppId = m[1];
    // A fresh tenant write is worth an immediate center-panel refresh.
    if (/Published to Intune|Assigned|Deployment complete/i.test(text)) {
      pollIntune();
    }
  }

  // ---- Intake --------------------------------------------------------------
  async function submitFile(file) {
    if (!file || !isAllowedName(file.name)) {
      flashConsoleError(
        `Ignored '${(file && file.name) || "file"}'${file ? ` (${extOf(file.name) || "no extension"})` : ""} — ` +
        "only .msi, .exe, or .zip installers are accepted.");
      $("filepick").value = "";  // allow re-picking after a bad selection
      return;
    }
    // A dropped file resuming an awaiting-installer queue item routes to the
    // queue installer endpoint (keeps the gated/test posture), not normal intake.
    if (pendingQueueInstaller) {
      const jid = pendingQueueInstaller;
      pendingQueueInstaller = null;
      $("dropzone").classList.remove("drag");
      const fd = new FormData();
      fd.append("file", file);
      try {
        const r = await fetch(`/api/demo/queue/${jid}/installer`, { method: "POST", body: fd });
        const data = await r.json();
        if (data.error) { flashConsoleError(data.error); return; }
        openStream(jid, `queue ← ${file.name}`);
      } catch (e) { flashConsoleError(String(e)); }
      return;
    }
    if (busy) {
      flashConsoleError("An action is already running — cancel it first (right panel).");
      return;
    }
    // A dropped file completing a URL-unavailable upgrade routes to the upgrade
    // endpoint (keeps the supersedence + scope), not normal intake.
    if (pendingUpgrade) {
      const pu = pendingUpgrade;
      pendingUpgrade = null;
      $("dropzone").classList.remove("drag");
      const fd = new FormData();
      fd.append("file", file);
      fd.append("app_id", pu.app_id);
      fd.append("scope", pu.scope);
      fd.append("force", "true");  // same upgrade resuming after a manual drop
      await postUpgradeFile(fd, `upgrade ← ${file.name}`);
      return;
    }
    gateMode = $("gate").checked;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("gate", gateMode ? "true" : "false");
    const mode = $("mode").value;
    if (mode) fd.append("mode", mode);
    await postJob(() => fetch("/api/demo/jobs", { method: "POST", body: fd }), file.name);
  }

  async function submitJson(body, label) {
    if (busy) {
      flashConsoleError("An action is already running — cancel it first (right panel).");
      return;
    }
    gateMode = $("gate").checked;
    body.gate = gateMode;
    const mode = $("mode").value;
    if (mode) body.mode = mode;
    await postJob(
      () => fetch("/api/demo/jobs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
      label,
    );
  }

  async function postJob(fetcher, label) {
    try {
      const r = await fetcher();
      const data = await r.json();
      if (data.error) { flashConsoleError(data.error); return; }
      const name = (data.analysis && (data.analysis.product_name || data.analysis.filename)) || label;
      openStream(data.job_id, name);
      const branchMsg = data.branch === "miss"
        ? "Catalog miss — research agent will run."
        : data.branch === "hit"
        ? "Catalog hit — deterministic package."
        : data.branch === "substituted"
        ? "Consumer build — fetching the enterprise version on your behalf…"
        : data.branch === "escalate"
        ? "Known non-packageable installer — escalating for engineer review (nothing will be installed)."
        : "Driver job created.";
      appendLine({ ts: "", source: "system", text: branchMsg });
    } catch (e) {
      flashConsoleError(String(e));
    }
  }

  function flashConsoleError(msg) {
    if (!currentJob) {
      $("job-app").textContent = "intake error";
    }
    appendLine({ source: "system", level: "error", text: msg });
  }

  function wireIntake() {
    const dz = $("dropzone");
    const pick = $("filepick");
    // Single open path: the dropzone (incl. the "browse" text) opens the picker.
    // The "browse" element is a plain span — NOT a <label for>, which would
    // ALSO trigger the input and pop the dialog twice.
    dz.addEventListener("click", () => pick.click());
    pick.addEventListener("change", () => {
      const f = pick.files[0];
      // Reset so the change event fires again even if the same file is re-picked.
      pick.value = "";
      if (f) submitFile(f);
    });

    ["dragenter", "dragover"].forEach((ev) =>
      window.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) =>
      window.addEventListener(ev, (e) => {
        e.preventDefault();
        if (ev === "drop" || e.target === document || e.relatedTarget === null)
          dz.classList.remove("drag");
      }));
    window.addEventListener("drop", (e) => {
      e.preventDefault();
      dz.classList.remove("drag");
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) submitFile(f);
    });

    $("url-go").addEventListener("click", () => {
      const url = $("url").value.trim();
      if (!url) return;
      if (!isAllowedName(url)) {
        flashConsoleError(
          "That URL doesn't point to a .msi, .exe, or .zip — paste a direct installer link.");
        return;
      }
      submitJson({ url }, url.split("/").pop().split("?")[0] || url);
    });
    $("driver-go").addEventListener("click", () => {
      const vendor = $("d-vendor").value.trim();
      const model = $("d-model").value.trim();
      if (!vendor || !model) { flashConsoleError("vendor and model required"); return; }
      submitJson({ vendor, model, driver_type: $("d-type").value.trim() || null },
                 `${vendor} ${model}`);
    });

    $("approve").addEventListener("click", async () => {
      if (!currentJob) return;
      $("gate-box").classList.add("hidden");
      await fetch(`/api/demo/jobs/${currentJob}/approve`, { method: "POST" });
    });

    $("verify").addEventListener("click", async () => {
      const q = lastAppId ? `?app_id=${encodeURIComponent(lastAppId)}` : "";
      const r = await fetch("/api/demo/intune/verify-url" + q);
      const d = await r.json();
      window.open(d.url, "_blank", "noopener");
    });

    // Software-gap delta modal + packaging queue.
    $("software-gap").addEventListener("click", openSoftwareGap);
    $("gap-cancel").addEventListener("click", () => $("gap-overlay").classList.add("hidden"));
    $("gap-overlay").addEventListener("click", (e) => {
      if (e.target === $("gap-overlay")) $("gap-overlay").classList.add("hidden");
    });
    $("gap-select-all").addEventListener("change", toggleGapSelectAll);
    $("gap-queue").addEventListener("click", queueSelected);

    // Right-panel Cancel — aborts the in-flight action (single job or batch).
    $("action-cancel").addEventListener("click", cancelCurrentAction);

    // Confirm/reject an agent-found installer URL.
    $("cf-confirm").addEventListener("click", confirmFoundUrl);
    $("cf-reject").addEventListener("click", rejectFoundUrl);

    // Upgrade scope dialog — exactly two choices (spec §4).
    $("scope-all").addEventListener("click", () => chooseScope("all"));
    $("scope-test").addEventListener("click", () => chooseScope("test"));
    $("scope-cancel").addEventListener("click", closeScopeDialog);
    $("scope-overlay").addEventListener("click", (e) => {
      if (e.target === $("scope-overlay")) closeScopeDialog();
    });
  }

  // ---- Software gap (installed but not packaged) ---------------------------
  function gapChip(cls, text) { return `<span class="gap-chip ${cls}">${esc(text)}</span>`; }

  async function openSoftwareGap() {
    if (busy) return;  // can't queue while another action runs
    $("gap-overlay").classList.remove("hidden");
    $("gap-counts").innerHTML = "";
    $("gap-note").classList.add("hidden");
    $("gap-body").innerHTML = `<tr class="empty"><td colspan="6">Scanning inventory…</td></tr>`;
    updateGapSelection();
    try {
      const r = await fetch("/api/demo/intune/software-delta?source=both");
      const d = await r.json();
      renderSoftwareGap(d);
    } catch (e) {
      $("gap-body").innerHTML = `<tr class="empty"><td colspan="6">Error: ${esc(String(e))}</td></tr>`;
    }
  }

  function renderSoftwareGap(d) {
    const c = d.counts || {};
    $("gap-counts").innerHTML =
      gapChip("candidate", `${c.unmanaged_candidate || 0} unmanaged candidates`) +
      gapChip("known", `${c.known_packageable || 0} known-packageable`) +
      gapChip("os", `${c.standard_os_component || 0} standard OS`) +
      gapChip("os", `${c.store_app || 0} store/MSIX`) +
      gapChip("managed", `${c.managed || 0} managed`);
    if (d.intune_unavailable) {
      $("gap-note").textContent =
        "Intune Detected Apps unavailable (service principal needs DeviceManagementManagedDevices.Read.All) — showing local device ARP only.";
      $("gap-note").classList.remove("hidden");
    }
    // Both actionable buckets are queueable: unmanaged candidates (need
    // research/acquisition) and known-packageable (catalog already knows them).
    const cands = (d.candidates || []).map((r) => ({ ...r, bucket: "unmanaged_candidate" }));
    const known = (d.known_packageable || []).map((r) => ({ ...r, bucket: "known_packageable" }));
    const rows = cands.concat(known);
    if (!rows.length) {
      $("gap-body").innerHTML = `<tr class="empty"><td colspan="6">No queueable software — everything installed is managed or standard OS.</td></tr>`;
      $("gap-select-all").checked = false;
      updateGapSelection();
      return;
    }
    $("gap-body").innerHTML = "";
    for (const a of rows) {
      const tr = document.createElement("tr");
      const isKnown = a.bucket === "known_packageable";
      tr.innerHTML =
        `<td class="gap-check"><input type="checkbox" class="gap-row-check" /></td>` +
        `<td class="app-name"></td>` +
        `<td>${esc(a.publisher || "")}</td>` +
        `<td>${esc(a.version || "")}</td>` +
        `<td>${gapChip(isKnown ? "known" : "candidate", isKnown ? "known-packageable" : "candidate")}</td>` +
        `<td>${a.device_count != null ? esc(String(a.device_count)) : "—"}</td>`;
      tr.querySelector(".app-name").textContent = a.name || "";
      // Stash the identity the queue endpoint needs on the row's checkbox.
      const cb = tr.querySelector(".gap-row-check");
      cb._candidate = {
        name: a.name, publisher: a.publisher || null, version: a.version || null,
        bucket: a.bucket, in_catalog: a.in_catalog || null,
        device_count: a.device_count != null ? a.device_count : null,
      };
      cb.addEventListener("change", updateGapSelection);
      $("gap-body").appendChild(tr);
    }
    $("gap-select-all").checked = false;
    updateGapSelection();
  }

  function selectedGapCandidates() {
    return Array.from(document.querySelectorAll(".gap-row-check"))
      .filter((cb) => cb.checked && cb._candidate)
      .map((cb) => cb._candidate);
  }

  function updateGapSelection() {
    const n = selectedGapCandidates().length;
    const btn = $("gap-queue");
    if (btn) {
      btn.disabled = n === 0;
      btn.textContent = n > 0
        ? `Queue ${n} for packaging`
        : "Queue selected for packaging";
    }
    const note = $("gap-sel-note");
    if (note) {
      note.textContent = n > 0
        ? `${n} selected — gated · Ring 0 (test). Processed one at a time.`
        : "Select software to queue for packaging.";
    }
  }

  function toggleGapSelectAll(e) {
    const on = !!e.target.checked;
    document.querySelectorAll(".gap-row-check").forEach((cb) => { cb.checked = on; });
    updateGapSelection();
  }

  // ---- Queue batch (the packaging queue from the delta) --------------------
  async function queueSelected() {
    if (busy) return;
    const items = selectedGapCandidates();
    if (!items.length) return;
    $("gap-overlay").classList.add("hidden");
    try {
      const r = await fetch("/api/demo/queue", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items, mode: $("mode").value || undefined }),
      });
      const data = await r.json();
      if (data.error) { flashConsoleError(data.error); return; }
      const jobs = data.jobs || [];
      if (!jobs.length) { flashConsoleError("Nothing was queued."); return; }
      activeBatch = { jobs, idx: 0, job_ids: jobs.map((j) => j.job_id), batch_id: data.batch_id };
      appendLine({ source: "system", text:
        `Queued ${jobs.length} item(s) for packaging (gated · Ring 0). Processing one at a time…` });
      streamBatchItem(0);
    } catch (e) {
      flashConsoleError(String(e));
    }
  }

  function streamBatchItem(i) {
    if (!activeBatch) return;
    if (i >= activeBatch.jobs.length) { finishBatch(); return; }
    activeBatch.idx = i;
    const job = activeBatch.jobs[i];
    // Queue items are ALWAYS gated server-side — reflect that client-side so the
    // gate-box (Approve ▶ Ring 0) reveals when this item passes testing.
    gateMode = true;
    openStream(job.job_id, job.name || `item ${i + 1}`);
  }

  // ---- Confirm an agent-found installer URL --------------------------------
  function showConfirmBox(jobId, url, provenance, confidence) {
    pendingConfirm = { job_id: jobId, url: url || "" };
    $("cf-url").value = url || "";
    $("cf-meta").textContent =
      `Confidence: ${confidence || "unknown"} · Source: ${provenance || "agent search"}`;
    $("confirm-box").classList.remove("hidden");
  }

  async function confirmFoundUrl() {
    if (!pendingConfirm) return;
    const jobId = pendingConfirm.job_id;
    const url = ($("cf-url").value || "").trim();
    $("confirm-box").classList.add("hidden");
    pendingConfirm = null;
    try {
      const r = await fetch(`/api/demo/queue/${jobId}/confirm-url`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url || null }),
      });
      const data = await r.json();
      if (data.error) { flashConsoleError(data.error); return; }
      openStream(jobId, "confirmed installer");
    } catch (e) {
      flashConsoleError(String(e));
    }
  }

  async function rejectFoundUrl() {
    if (!pendingConfirm) return;
    const jobId = pendingConfirm.job_id;
    $("confirm-box").classList.add("hidden");
    pendingConfirm = null;
    try { await fetch(`/api/demo/jobs/${jobId}/cancel`, { method: "POST" }); } catch (e) { /* ignore */ }
    appendLine({ source: "system", level: "warn",
      text: `Rejected the agent-found URL for job #${jobId} — cancelled. Drop an installer to package it manually.` });
  }

  // ---- Cancel the in-flight action -----------------------------------------
  async function cancelCurrentAction() {
    const bar = $("action-cancel");
    if (bar) bar.disabled = true;
    try {
      if (activeBatch) {
        const ids = activeBatch.job_ids;
        const batch = activeBatch;
        activeBatch = null;  // stop client-side advancement immediately
        if (evtSource) { evtSource.close(); evtSource = null; }
        await fetch("/api/demo/queue/cancel", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job_ids: ids }),
        });
        appendLine({ source: "system", level: "warn", text:
          `Queue cancelled — ${batch.jobs.length} item(s) stopped.` });
      } else if (currentJob) {
        const jid = currentJob;
        if (evtSource) { evtSource.close(); evtSource = null; }
        await fetch(`/api/demo/jobs/${jid}/cancel`, { method: "POST" });
        appendLine({ source: "system", level: "warn", text: `Cancelled job #${jid}.` });
      }
    } catch (e) {
      flashConsoleError(String(e));
    } finally {
      if (bar) bar.disabled = false;
      setBusy(false);
      pollIntune();
    }
  }

  // ---- Intune center panel -------------------------------------------------
  async function pollIntune() {
    try {
      const r = await fetch("/api/demo/intune/apps");
      const view = await r.json();
      renderIntune(view);
    } catch (e) {
      // leave the last good render in place
    }
  }

  function renderIntune(view) {
    lastView = view;
    const badge = $("intune-mode");
    badge.textContent = view.mode === "live" ? "live tenant" : "fixture mode";
    badge.dataset.mode = view.mode;
    const body = $("intune-body");
    const apps = view.apps || [];
    if (!apps.length) {
      body.innerHTML = `<tr class="empty"><td colspan="6">No Win32 apps ${view.mode === "fixture" ? "in fixtures" : "in tenant"} yet</td></tr>`;
      return;
    }
    body.innerHTML = "";
    for (const app of apps) {
      const tr = document.createElement("tr");
      const isFresh = !firstIntuneLoad && app.id && !seenAppIds.has(app.id);
      if (isFresh) tr.classList.add("fresh");
      if (app.id) seenAppIds.add(app.id);
      const assign = (app.assignments && app.assignments.length)
        ? app.assignments.map((a) => `<span class="ring-chip">${esc(a.ring)}</span>`).join(" ")
        : `<span style="color:var(--text-mute)">—</span>`;
      tr.innerHTML =
        `<td class="app-name"></td>` +
        `<td>${esc(app.version || "")}</td>` +
        `<td>${esc(app.publisher || "")}</td>` +
        `<td>${assign}</td>` +
        `<td>${esc(app.created || "")}</td>` +
        `<td class="ver-cell"></td>`;
      tr.querySelector(".app-name").textContent = app.name || "(unnamed)";
      buildVersionCell(tr.querySelector(".ver-cell"), app);
      body.appendChild(tr);
    }
    firstIntuneLoad = false;
  }

  // ---- Version state: refresh + supersedence badge (spec §4) ---------------
  function badgeFromServerState(app) {
    switch (app.version_state) {
      case "pending":  return { state: "pending", label: "Pending" };
      case "":         return null;
      case "current":  return { state: "current", label: "Current" };
      default:
        // "N-1", "N-2", … come through verbatim as superseded labels.
        if (/^N-\d+$/.test(app.version_state || "")) {
          return { state: "superseded", label: app.version_state };
        }
        return { state: "current", label: "Current" };
    }
  }

  function buildVersionCell(cell, app) {
    cell.innerHTML = "";
    if (!app.id) return;
    const btn = document.createElement("button");
    btn.className = "ver-refresh";
    btn.title = "Check the vendor source for a newer version";
    btn.textContent = "↻";
    btn.addEventListener("click", () => refreshVersion(app, btn));
    cell.appendChild(btn);

    const state = badgeOverride.get(app.id) || badgeFromServerState(app);
    if (!state) return;
    const b = document.createElement("span");
    b.className = "ver-badge";
    b.dataset.state = state.state;
    b.textContent = state.label;
    if (state.state === "available") {
      b.title = "Click to package & deploy this upgrade";
      b.addEventListener("click", () => openScopeDialog(app));
    }
    cell.appendChild(b);
  }

  function rerender() { if (lastView) renderIntune(lastView); }

  async function refreshVersion(app, btn) {
    if (!app.id) return;
    if (busy) return;  // one action at a time
    if (btn) { btn.disabled = true; btn.classList.add("spin"); }
    badgeOverride.set(app.id, { state: "checking", label: "Checking…" });
    rerender();
    setLamp("thinking", "checking vendor source…");
    try {
      const r = await fetch("/api/demo/intune/check-version", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          app_id: app.id, app_label: app.name,
          current_version: app.current_version || app.version || null,
          mode: $("mode").value || undefined,
        }),
      });
      const res = await r.json();
      setLamp("ready", "authenticated · standing by");
      if (res.is_newer && res.latest_version) {
        appMeta.set(app.id, {
          entry_id: res.entry_id, latest_version: res.latest_version,
          download_url: res.download_url, current_version: res.current_version,
        });
        badgeOverride.set(app.id, {
          state: "available", label: `New version available (${res.latest_version})`,
        });
        appendLine({ source: "system", text:
          `${app.name}: new version ${res.latest_version} available (deployed ${res.current_version || "?"}).` });
      } else {
        badgeOverride.delete(app.id);
        appendLine({ source: "system", text:
          `${app.name}: up to date (${res.current_version || app.version || "unknown"}).` });
      }
    } catch (e) {
      badgeOverride.delete(app.id);
      setLamp("ready", "authenticated · standing by");
      appendLine({ source: "system", level: "error", text: `Version check failed: ${e}` });
    } finally {
      if (btn) { btn.disabled = false; btn.classList.remove("spin"); }
      rerender();
    }
  }

  // ---- Upgrade scope dialog ------------------------------------------------
  function openScopeDialog(app) {
    if (busy) return;  // one action at a time
    scopeApp = app;
    const meta = appMeta.get(app.id) || {};
    $("scope-title").textContent = "Package and deploy";
    $("scope-msg").textContent =
      `Package and deploy ${app.name}${meta.latest_version ? " " + meta.latest_version : ""}?`;
    $("scope-overlay").classList.remove("hidden");
  }
  function closeScopeDialog() {
    scopeApp = null;
    $("scope-overlay").classList.add("hidden");
  }

  async function chooseScope(scope) {
    const app = scopeApp;
    closeScopeDialog();
    if (!app) return;
    gateMode = $("gate").checked;
    // Hand off to the pipeline + server-derived state from here.
    badgeOverride.delete(app.id);
    await postUpgrade(app, scope, false);
  }

  async function postUpgrade(app, scope, force) {
    const meta = appMeta.get(app.id) || {};
    try {
      const r = await fetch("/api/demo/intune/upgrade", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          app_id: app.id, scope,
          download_url: meta.download_url || null,
          old_entry_id: meta.entry_id || null,
          mode: $("mode").value || undefined,
          gate: gateMode,
          force: !!force,
        }),
      });
      const data = await r.json();
      if (data.error) { flashConsoleError(data.error); return; }
      // Soft concurrency guard: an upgrade for this product is already running.
      // Warn and let the operator decide — they can proceed deliberately.
      if (data.in_flight && !force) {
        const ok = window.confirm(
          data.warning || "An upgrade for this app is already in progress. Start another anyway?");
        if (!ok) {
          appendLine({ source: "system", level: "warn",
            text: "Upgrade cancelled — one is already in progress for this app." });
          return;
        }
        return postUpgrade(app, scope, true);
      }
      if (data.awaiting_upload) {
        pendingUpgrade = { app_id: app.id, scope };
        appendLine({ source: "system", level: "warn", text:
          "Source unavailable — drop the newer installer onto the strip to continue the upgrade." });
        $("dropzone").classList.add("drag");
        return;
      }
      openStream(data.job_id, `${app.name} → ${meta.latest_version || "newer version"}`);
    } catch (e) {
      flashConsoleError(String(e));
    }
  }

  async function postUpgradeFile(fd, label) {
    try {
      const r = await fetch("/api/demo/intune/upgrade", { method: "POST", body: fd });
      const data = await r.json();
      if (data.error) { flashConsoleError(data.error); return; }
      openStream(data.job_id, label);
    } catch (e) {
      flashConsoleError(String(e));
    }
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  // ---- Boot ----------------------------------------------------------------
  function boot() {
    wireIntake();
    preflight();
    pollIntune();
    setInterval(pollIntune, 4000);
    setInterval(preflight, 30000); // keep readiness lights honest
  }
  document.addEventListener("DOMContentLoaded", boot);
})();
