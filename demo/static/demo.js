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
    resetStepper();
    advanceStepper("pending");
    $("console").innerHTML = "";
    $("job-head").querySelector(".job-id").textContent = "job #" + jobId;
    if (appName) $("job-app").textContent = appName;
    $("gate-box").classList.add("hidden");

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
        break;
      case "console":
        appendLine(env);
        if (env.state) advanceStepper(env.state);
        captureSideEffects(env);
        break;
      case "end":
        if (evtSource) { evtSource.close(); evtSource = null; }
        pollIntune(); // final refresh — the payoff shot
        break;
      case "hello":
      default:
        break;
    }
  }

  function captureSideEffects(env) {
    const text = env.text || "";
    // Reveal the approval gate when testing passes in gate mode.
    if (gateMode && env.gate === true) {
      $("gate-box").classList.remove("hidden");
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
    gateMode = $("gate").checked;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("gate", gateMode ? "true" : "false");
    const mode = $("mode").value;
    if (mode) fd.append("mode", mode);
    await postJob(() => fetch("/api/demo/jobs", { method: "POST", body: fd }), file.name);
  }

  async function submitJson(body, label) {
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
    const badge = $("intune-mode");
    badge.textContent = view.mode === "live" ? "live tenant" : "fixture mode";
    badge.dataset.mode = view.mode;
    const body = $("intune-body");
    const apps = view.apps || [];
    if (!apps.length) {
      body.innerHTML = `<tr class="empty"><td colspan="5">No Win32 apps ${view.mode === "fixture" ? "in fixtures" : "in tenant"} yet</td></tr>`;
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
        `<td>${esc(app.created || "")}</td>`;
      tr.querySelector(".app-name").textContent = app.name || "(unnamed)";
      body.appendChild(tr);
    }
    firstIntuneLoad = false;
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
