/* AutoPackager — Mission Control demo console.
   One SSE stream per job multiplexes pipeline + AutoPackager research lines; the AI lamp and
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
  let cveApp = null;                   // app the open CVE risk drawer targets

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
  // Source display labels. The research bridge's wire source is "autopackager"
  // — the customer-facing brand. (Older payloads used "claude"; map it too so a
  // mixed stream never surfaces the engine name.)
  const SRC_LABELS = { autopackager: "AutoPackager", claude: "AutoPackager", pipeline: "Pipeline", system: "System" };

  function appendLine(env) {
    const con = $("console");
    const line = document.createElement("div");
    line.className = "cline " + (env.level || "info") +
      (env.source === "autopackager" || env.source === "claude" ? " autopackager" : "");
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
    pollIntune(true); // force a live reload — the payoff shot

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
    pollIntune(true);
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
    // A fresh tenant write is worth an immediate center-panel reload.
    if (/Published to Intune|Assigned|Deployment complete/i.test(text)) {
      pollIntune(true);
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
      // Approval PUBLISHES to the tenant (Ring 0) — confirm to guard against an
      // accidental click writing to Intune.
      if (!window.confirm("Approve & publish this package to Ring 0? This deploys it to the tenant.")) return;
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
    $("intune-refresh").addEventListener("click", () => pollIntune(true));
    $("check-updates").addEventListener("click", checkAllUpdates);
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

    // CVE risk drawer.
    $("cve-cancel").addEventListener("click", closeCveDrawer);
    $("cve-rescan").addEventListener("click", rescanCveLive);
    $("cve-patch").addEventListener("click", () => { if (cveApp) patchNow(cveApp); });
    $("cve-overlay").addEventListener("click", (e) => {
      if (e.target === $("cve-overlay")) closeCveDrawer();
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
      // Known-vulnerable installed-but-unmanaged software gets a severity chip,
      // so the gap modal also reads as a risk worklist.
      if (a.cve && a.cve.cve_count) {
        const { sev, score } = sevMeta(a.cve);
        const chip = document.createElement("button");
        chip.className = "risk-badge inline";
        chip.dataset.sev = sev;
        chip.innerHTML = `<span class="risk-dot"></span>${sev.toUpperCase()} ${score}`;
        chip.title = `${a.cve.cve_count} CVE${a.cve.cve_count > 1 ? "s" : ""} — click for detail`;
        chip.addEventListener("click", (e) => {
          e.stopPropagation();
          openCveDrawer({ name: a.name, version: a.version, current_version: a.version,
                          id: null, cve: a.cve });
        });
        tr.querySelector(".app-name").appendChild(chip);
      }
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
      // Multi-package batches go to the dedicated batch-stream page: a live
      // grid where every package is watched AND approved independently, so the
      // single-action console never becomes the bottleneck (gates/confirms for
      // different items no longer interrupt each other). A single item stays on
      // the console for the focused, full-detail view.
      if (jobs.length > 1) {
        const url = `/demo/stream?batch=${data.batch_id}`;
        appendLine({ source: "system", text:
          `Queued ${jobs.length} packages (gated · Ring 0). Opening the batch stream — watch & approve all of them there: ${url}` });
        showBatchPill(data.batch_id);   // header pill to return to this batch anytime
        window.open(url, "_blank", "noopener");
        return;
      }
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
      pollIntune(true);
    }
  }

  // ---- Intune center panel -------------------------------------------------
  // Background polls are served from the server's stale-while-revalidate cache
  // (instant). A FORCE poll (?refresh=1) does a full live reload — used after a
  // tenant write (the payoff shot) and by the manual Refresh button. We never
  // run two force-reloads at once.
  let intuneForcing = false;
  async function pollIntune(force = false) {
    if (force && intuneForcing) return;
    if (force) { intuneForcing = true; setIntuneRefreshing(true); }
    try {
      const url = force ? "/api/demo/intune/apps?refresh=1" : "/api/demo/intune/apps";
      const r = await fetch(url);
      const view = await r.json();
      renderIntune(view);
    } catch (e) {
      // leave the last good render in place
    } finally {
      if (force) { intuneForcing = false; setIntuneRefreshing(false); }
    }
  }

  // Freshness indicator in the center header: "live · just now" / "cached 12s"
  // and a spinning state while a background revalidate or forced reload runs.
  function setIntuneRefreshing(on) {
    const el = $("intune-fresh");
    if (el) el.classList.toggle("refreshing", !!on);
  }
  function updateFreshness(view) {
    const el = $("intune-fresh");
    if (!el) return;
    const c = view.cache || {};
    let label;
    if (c.source === "live" || c.hit === false) label = "live · just now";
    else if (c.source === "snapshot") label = "restored · updating…";
    else if (c.age_s == null) label = "cached";
    else if (c.age_s < 2) label = "live · just now";
    else label = `cached · ${Math.round(c.age_s)}s`;
    el.textContent = label;
    el.classList.toggle("refreshing", !!c.revalidating);
    el.dataset.stale = (c.age_s != null && c.age_s >= 25) ? "1" : "0";
  }

  function renderIntune(view) {
    lastView = view;
    const badge = $("intune-mode");
    badge.textContent = view.mode === "live" ? "live tenant" : "fixture mode";
    badge.dataset.mode = view.mode;
    updateFreshness(view);
    const body = $("intune-body");
    const apps = riskSorted(view.apps || []);
    if (!apps.length) {
      body.innerHTML = `<tr class="empty"><td colspan="7">No Win32 apps ${view.mode === "fixture" ? "in fixtures" : "in tenant"} yet</td></tr>`;
      return;
    }
    renderRiskSummary(apps);
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
        `<td class="risk-cell"></td>` +
        `<td>${esc(app.publisher || "")}</td>` +
        `<td>${assign}</td>` +
        `<td>${esc(app.created || "")}</td>` +
        `<td class="ver-cell"></td>`;
      tr.querySelector(".app-name").textContent = app.name || "(unnamed)";
      buildRiskCell(tr.querySelector(".risk-cell"), app);
      buildVersionCell(tr.querySelector(".ver-cell"), app);
      body.appendChild(tr);
    }
    firstIntuneLoad = false;
  }

  // ---- CVE risk: severity rank, sort, badge, drawer ------------------------
  const SEV_RANK = { critical: 4, high: 3, medium: 2, low: 1, none: 0, unknown: -1 };

  // Estate exposure roll-up in the center-panel header.
  function renderRiskSummary(apps) {
    const el = $("risk-summary");
    if (!el) return;
    const tally = { critical: 0, high: 0, medium: 0, low: 0 };
    let exposed = 0;
    for (const a of apps) {
      const c = a.cve;
      if (c && c.cve_count && tally[c.severity] != null) { tally[c.severity]++; exposed++; }
    }
    if (!exposed) {
      el.dataset.sev = "none";
      el.textContent = "✓ no known exposure";
      return;
    }
    el.dataset.sev = tally.critical ? "critical" : tally.high ? "high" : tally.medium ? "medium" : "low";
    const parts = [];
    for (const s of ["critical", "high", "medium", "low"]) {
      if (tally[s]) parts.push(`${tally[s]} ${s}`);
    }
    el.textContent = `⚠ ${parts.join(" · ")}`;
  }

  function riskScore(app) {
    const c = app && app.cve;
    if (!c) return -1;
    return (SEV_RANK[c.severity] ?? -1) * 100 + (c.max_cvss || 0);
  }

  // Worst-first, but keep the server's order as a stable tiebreak so equal-risk
  // rows (and the "fresh" highlight) stay where the operator expects them.
  function riskSorted(apps) {
    return apps
      .map((a, i) => [a, i])
      .sort((x, y) => (riskScore(y[0]) - riskScore(x[0])) || (x[1] - y[1]))
      .map((p) => p[0]);
  }

  function sevMeta(c) {
    const sev = (c && c.severity) || "unknown";
    const score = c && (c.max_cvss != null) ? c.max_cvss.toFixed(1) : "";
    return { sev, score };
  }

  // "Patch now" is only meaningful on the LATEST deployed version of a product.
  // For an N-1/N-2 the fix already exists in the estate — the new version is
  // confirmed/deployed (so patching is done) and a clean N-1 is retire-eligible,
  // not patch-eligible. So we only offer Patch now on the current/Latest.
  function isLatestVersion(app) {
    const s = app && app.version_state;
    return !/^N-\d+$/.test(s || "");
  }

  function buildRiskCell(cell, app) {
    cell.innerHTML = "";
    const c = app.cve;
    if (!c || c.source === "none") {
      const dash = document.createElement("span");
      dash.className = "risk-none-data";
      dash.textContent = "—";
      dash.title = "No CVE data for this product (curated cache miss). Re-scan live to query NVD.";
      cell.appendChild(dash);
      return;
    }
    if (!c.cve_count) {
      const ok = document.createElement("span");
      ok.className = "risk-clean";
      ok.textContent = "✓ no known CVEs";
      ok.title = `Deployed ${app.current_version || app.version || ""} — no public CVEs a newer release fixes.`;
      cell.appendChild(ok);
      return;
    }
    const { sev, score } = sevMeta(c);
    const badge = document.createElement("button");
    badge.className = "risk-badge";
    badge.dataset.sev = sev;
    badge.innerHTML = `<span class="risk-dot"></span>${sev.toUpperCase()} ${score}`;
    badge.title = `${c.cve_count} CVE${c.cve_count > 1 ? "s" : ""} fixed by upgrading — click for detail`;
    badge.addEventListener("click", () => openCveDrawer(app));
    cell.appendChild(badge);

    const count = document.createElement("span");
    count.className = "risk-count";
    count.textContent = `${c.cve_count} CVE${c.cve_count > 1 ? "s" : ""}`;
    cell.appendChild(count);

    // Patch now only on the Latest — an N-1/N-2 is already superseded by a newer
    // deployed version (and a clean one is for retirement, not patching).
    if (isLatestVersion(app)) {
      const patch = document.createElement("button");
      patch.className = "risk-patch";
      patch.textContent = "Patch now";
      patch.title = "Check the vendor source and patch through the pipeline";
      patch.addEventListener("click", () => patchNow(app));
      cell.appendChild(patch);
    }
  }

  // ---- Version state: refresh + supersedence badge -------------------------
  // The server ranks every deployed app in a product line by displayVersion, so
  // the top of each chain is "Latest" and older deployed versions are N-1/N-2…
  function badgeFromServerState(app) {
    switch (app.version_state) {
      case "pending":  return { state: "pending", label: "Pending" };
      case "retired":  return { state: "retired", label: "Retired" };
      case "":         return { state: "current", label: "Latest" };
      case "current":  return { state: "current", label: "Latest" };
      default:
        // "N-1", "N-2", … come through verbatim as superseded labels.
        if (/^N-\d+$/.test(app.version_state || "")) {
          return { state: "superseded", label: app.version_state };
        }
        return { state: "current", label: "Latest" };
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

    // Install count — the lifecycle "clean" signal. For an old version (N-1/N-2)
    // 0 installs means retire-eligible ("clean"); installs remaining are the
    // drain target. For the Latest, the count is just rollout progress.
    const inst = installLine(app);
    if (inst) {
      const el = document.createElement("span");
      el.className = "inst-pill " + inst.cls;
      el.textContent = inst.text;
      if (inst.title) el.title = inst.title;
      cell.appendChild(el);
    }

    // Per-product autoupdate toggle — only on the Latest (it's the product's
    // policy). On = full auto-upgrade when a newer version is found; off = gated.
    if (isLatestVersion(app)) {
      const tog = document.createElement("button");
      tog.className = "auto-toggle";
      tog.dataset.on = app.auto_update ? "1" : "0";
      tog.textContent = app.auto_update ? "auto ⟳ on" : "auto ⟳ off";
      tog.title = app.auto_update
        ? "Autoupdate ON — new versions deploy automatically. Click to require approval (gated)."
        : "Autoupdate OFF — new versions are packaged and held at the Ring 0 gate. Click to deploy automatically.";
      tog.addEventListener("click", (e) => { e.stopPropagation(); setAutoupdate(app, !app.auto_update); });
      cell.appendChild(tog);
    }
  }

  async function setAutoupdate(app, enabled) {
    if (!app.id) return;
    try {
      const r = await fetch(`/api/demo/intune/${encodeURIComponent(app.id)}/autoupdate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      const res = await r.json();
      if (res.error) throw new Error(res.error);
      app.auto_update = !!res.auto_update;
      appendLine({ source: "system", text:
        `${app.name}: autoupdate ${app.auto_update ? "ENABLED (full auto)" : "disabled (gated)"}.` });
      rerender();
    } catch (e) {
      appendLine({ source: "system", level: "error", text: `Autoupdate toggle failed: ${e}` });
    }
  }

  // The discovery loop on demand: check every Latest app (catalog → internet)
  // and act per its autoupdate setting (full-auto or gated). Surfaces a summary.
  async function checkAllUpdates() {
    if (busy) return;
    const btn = $("check-updates");
    btn.disabled = true; const orig = btn.textContent; btn.textContent = "Checking…";
    appendLine({ source: "system", text: "Checking catalog → internet for newer versions…" });
    try {
      const r = await fetch("/api/demo/intune/check-updates", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: $("mode").value || undefined }),
      });
      const res = await r.json();
      const plan = res.plan || [];
      const updates = plan.filter((p) => p.status === "update");
      if (!updates.length) {
        appendLine({ source: "system", text:
          `Checked ${res.checked} app(s) — all up to date. Nothing to upgrade.` });
      } else {
        for (const u of updates) {
          appendLine({ source: "system", text:
            `${u.name}: ${u.current_version} → ${u.latest_version} — ${u.action === "auto-upgrade" ? "auto-upgrading" : "gated (awaiting approval)"} (job #${u.job_id}).` });
        }
        appendLine({ source: "system", text:
          `${res.dispatched} upgrade(s) dispatched of ${res.checked} checked.` });
      }
      pollIntune(true);
    } catch (e) {
      appendLine({ source: "system", level: "error", text: `Update check failed: ${e}` });
    } finally {
      btn.disabled = false; btn.textContent = orig;
    }
  }

  // Returns {cls, text, title} for the install-count pill, or null when counts
  // are unavailable (so we never show a false "clean").
  function installLine(app) {
    const n = app.installed;
    if (n == null) return null;                       // counts unavailable
    const isOld = /^N-\d+$/.test(app.version_state || "");
    if (n === 0) {
      return isOld
        ? { cls: "clean", text: "✓ clean", title: "0 installs — safe to retire" }
        : { cls: "zero",  text: "0 installs", title: "not yet on any device" };
    }
    const word = n === 1 ? "install" : "installs";
    return isOld
      ? { cls: "stale", text: `${n} ${word}`, title: `${n} device(s) still on this old version` }
      : { cls: "ok",    text: `${n} ${word}`, title: `${n} device(s) have the latest` };
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
      } else if (res.already_deployed && res.latest_version) {
        // The "newer" build already exists in this product line — don't offer to
        // create a duplicate of an app already in the tenant.
        badgeOverride.delete(app.id);
        appendLine({ source: "system", text:
          `${app.name}: ${res.latest_version} is already in the tenant — not creating a duplicate.` });
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

  // ---- CVE risk drawer -----------------------------------------------------
  function openCveDrawer(app, cveOverride) {
    cveApp = app;
    const c = cveOverride || app.cve || {};
    const { sev, score } = sevMeta(c);
    $("cve-title").textContent = `${app.name || "App"} — security risk`;
    const sb = $("cve-summary-badge");
    sb.dataset.sev = sev;
    sb.textContent = c.cve_count
      ? `${sev.toUpperCase()} ${score}`
      : (c.source === "none" ? "no data" : "clean");
    const srcLabel = { cache: "curated CVE cache", nvd: "live NVD feed",
                       bridge: "AI research", none: "no source" }[c.source] || c.source;
    $("cve-sub").textContent = c.cve_count
      ? `${c.cve_count} CVE${c.cve_count > 1 ? "s" : ""} that a newer release fixes · ` +
        `deployed ${app.current_version || app.version || "?"} · source: ${srcLabel}`
      : (c.source === "none"
          ? "No CVE data for this product in the curated cache. Re-scan live to query NVD."
          : `No public CVEs a newer release fixes for ${app.current_version || app.version || "this version"}.`);
    const list = $("cve-list");
    list.innerHTML = "";
    for (const v of c.cves || []) {
      const row = document.createElement("div");
      row.className = "cve-row";
      row.dataset.sev = v.severity || "unknown";
      const fixed = v.fixed_in ? `<span class="cve-fixed">fixed in ${esc(v.fixed_in)}</span>` : "";
      const link = v.url
        ? `<a href="${esc(v.url)}" target="_blank" rel="noopener" class="cve-id">${esc(v.id)}</a>`
        : `<span class="cve-id">${esc(v.id || "")}</span>`;
      row.innerHTML =
        `<div class="cve-row-head">` +
          `<span class="cve-sev" data-sev="${esc(v.severity || "unknown")}">` +
            `${(v.severity || "?").toUpperCase()} ${v.cvss != null ? v.cvss.toFixed(1) : ""}</span>` +
          link + fixed +
        `</div>` +
        `<div class="cve-row-summary"></div>`;
      row.querySelector(".cve-row-summary").textContent = v.summary || "";
      list.appendChild(row);
    }
    // Patch-now / live re-scan act on a deployed Intune app (need its id); a row
    // opened from the software-gap modal has none — it's queued, not patched.
    const hasApp = !!app.id;
    // Patch now only on the Latest (an N-1/N-2 is already superseded / retire-eligible).
    $("cve-patch").style.display = (hasApp && c.cve_count && isLatestVersion(app)) ? "" : "none";
    $("cve-rescan").style.display = hasApp ? "" : "none";
    $("cve-overlay").classList.remove("hidden");
  }
  function closeCveDrawer() {
    cveApp = null;
    $("cve-overlay").classList.add("hidden");
  }
  async function rescanCveLive() {
    if (!cveApp || !cveApp.id) return;
    const btn = $("cve-rescan");
    btn.disabled = true; btn.textContent = "Scanning…";
    setLamp("thinking", "querying NVD…");
    try {
      const r = await fetch(`/api/demo/intune/${encodeURIComponent(cveApp.id)}/cves?mode=live`);
      const res = await r.json();
      if (res && res.cve) {
        cveApp.cve = res.cve;            // cache on the row so the table re-badges
        openCveDrawer(cveApp, res.cve);  // re-render the drawer with live data
        rerender();
      }
      setLamp("ready", "authenticated · standing by");
    } catch (e) {
      setLamp("ready", "authenticated · standing by");
      appendLine({ source: "system", level: "error", text: `Live CVE scan failed: ${e}` });
    } finally {
      btn.disabled = false; btn.textContent = "Re-scan live ↻";
    }
  }

  // "Patch now": check the vendor source for a newer release; if one exists,
  // open the existing package-and-deploy scope dialog (same supersedence → Ring 0
  // pipeline). Reuses refreshVersion's check + badge, then hands off.
  async function patchNow(app) {
    if (busy) return;
    closeCveDrawer();
    await refreshVersion(app);
    const meta = appMeta.get(app.id) || {};
    if (meta.latest_version) {
      openScopeDialog(app);
    } else {
      appendLine({ source: "system", text:
        `${app.name}: no newer build found upstream to patch the reported CVEs (already at the latest the source offers).` });
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

  async function postUpgrade(app, scope, force, overrideUrl) {
    const meta = appMeta.get(app.id) || {};
    if (!meta.download_url && !overrideUrl) {
      appendLine({ source: "autopackager", level: "info", text:
        "Looking for the newer installer — checking the known source, then a web search…" });
    }
    try {
      const r = await fetch("/api/demo/intune/upgrade", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          app_id: app.id, scope,
          download_url: overrideUrl || meta.download_url || null,
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
      // Agent found a candidate source via web search — UNTRUSTED. Confirm
      // before we download/install it (supply-chain guardrail).
      if (data.awaiting_confirm) {
        const conf = data.confidence ? ` · confidence: ${data.confidence}` : "";
        const prov = data.provenance ? `\nProvenance: ${data.provenance}` : "";
        appendLine({ source: "autopackager", level: "info", text:
          `Found a candidate source: ${data.proposed_url}${conf}` });
        const ok = window.confirm(
          `${data.message || "Confirm this source?"}\n\n${data.proposed_url}${prov}`);
        if (!ok) {
          appendLine({ source: "system", level: "warn",
            text: "Upgrade cancelled — agent-found source not confirmed." });
          return;
        }
        return postUpgrade(app, scope, force, data.proposed_url);
      }
      if (data.awaiting_upload) {
        pendingUpgrade = { app_id: app.id, scope };
        appendLine({ source: "system", level: "warn", text:
          data.message ||
          "No source found automatically — drop the newer installer onto the strip to continue." });
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

  // ---- Batch-stream pill (header) ------------------------------------------
  // A header button that appears whenever a queue batch exists, so the operator
  // can jump (back) to the multiplexed batch-stream view at any time — while it
  // runs AND after items succeed/fail (for review). Survives a console reload
  // via localStorage; a live snapshot poll keeps the count/active dot honest.
  const LS_BATCH = "ap_last_batch";
  let batchPollTimer = null;

  function openBatchStream(batchId) {
    window.open(`/demo/stream?batch=${batchId}`, "_blank", "noopener");
  }

  function showBatchPill(batchId) {
    if (!batchId) return;
    try { localStorage.setItem(LS_BATCH, batchId); } catch (e) { /* private mode */ }
    const pill = $("batch-pill");
    pill.classList.remove("hidden");
    pill.onclick = () => openBatchStream(batchId);
    refreshBatchPill(batchId);
    if (batchPollTimer) clearInterval(batchPollTimer);
    batchPollTimer = setInterval(() => refreshBatchPill(batchId), 5000);
  }

  async function refreshBatchPill(batchId) {
    const pill = $("batch-pill");
    const label = pill.querySelector(".bp-label");
    try {
      const r = await fetch(`/api/demo/queue/${batchId}/snapshot`);
      const data = await r.json();
      const jobs = data.jobs || [];
      if (!jobs.length) { label.textContent = "Batch stream"; pill.dataset.active = "false"; return; }
      const TERMINAL = new Set(["completed", "failed", "cancelled"]);
      const active = jobs.filter((j) => !TERMINAL.has(j.state)).length;
      pill.dataset.active = active > 0 ? "true" : "false";
      label.textContent = active > 0
        ? `Batch · ${active}/${jobs.length} running`
        : `Batch · ${jobs.length} done`;
    } catch (e) { /* keep last label on a transient error */ }
  }

  function restoreBatchPill() {
    let batchId = null;
    try { batchId = localStorage.getItem(LS_BATCH); } catch (e) { /* ignore */ }
    if (batchId) showBatchPill(batchId);
  }

  // ---- Boot ----------------------------------------------------------------
  function boot() {
    wireIntake();
    preflight();
    pollIntune();
    restoreBatchPill();
    setInterval(pollIntune, 4000);
    setInterval(preflight, 30000); // keep readiness lights honest
  }
  document.addEventListener("DOMContentLoaded", boot);
})();
