/* NuruXplore look-alike web app — frontend logic.
   Talks only to our backend (/api/local/*), which talks to the live API.
   The Bearer token never touches the browser. */

const COSTS = { proposal: 100, thesis: 600 };
const POLL_MS = 2000;
const POLL_MAX = 360; // ~12 minutes cap so we never spin forever

const $ = (sel) => document.querySelector(sel);
const state = {
  mode: "chat",
  resType: "proposal",
  projectUuid: null,
  resProjectUuid: null,
  profile: null,
  pollTimer: null,
  pollTries: 0,
};

// ---------------------------------------------------------------- http

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (_) {}
  return { ok: res.ok, status: res.status, data: data || {} };
}

// ------------------------------------------------------------ errors UI

function showBanner(msg, kind) {
  const el = $("#resError");
  el.hidden = false;
  el.className = "banner " + friendlyKind(kind);
  el.textContent = friendlyMessage(kind, msg);
}

function friendlyKind(kind) {
  return ["auth", "rate_limit", "out_of_credits", "network", "generation_failed", "http"].includes(kind)
    ? kind : "http";
}

function friendlyMessage(kind, msg) {
  const map = {
    auth: "Authentication failed — invalid or expired credentials.",
    rate_limit: "Rate limit reached. Wait a minute and try again.",
    out_of_credits: "Insufficient credits to complete that action.",
    network: "Network or timeout error — the API did not respond.",
    generation_failed: "Document generation failed — your credits were refunded.",
    http: "Something went wrong talking to the API.",
  };
  return (map[kind] || map.http) + (msg && kind === "http" ? ` (${msg})` : "");
}

function clearBanner() { $("#resError").hidden = true; }

// -------------------------------------------------------------- boot

async function boot() {
  const r = await api("/api/local/me", { method: "GET" });
  if (r.ok) { enterApp(r.data); }
  else { enterLogin(); }
}

function enterApp(me) {
  $("#loginScreen").hidden = true;
  $("#appScreen").hidden = false;
  const user = me.user || {};
  $("#userLabel").textContent = user.name || user.email || "";
  $("#userLabel").hidden = false;
  refreshCredits(me.credits_balance);
  $("#logoutBtn").hidden = false;
}

function enterLogin() {
  $("#loginScreen").hidden = false;
  $("#appScreen").hidden = true;
}

async function refreshCredits(credits) {
  $("#credits").hidden = false;
  $("#credits").textContent = `⚡ ${credits == null ? "--" : credits} credits`;
}

// ------------------------------------------------------------- auth UI

$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#loginError").hidden = true;
  const r = await api("/api/local/login", {
    method: "POST",
    body: { email: $("#loginEmail").value, password: $("#loginPassword").value },
  });
  if (r.ok) { enterApp(r.data); }
  else {
    $("#loginError").hidden = false;
    $("#loginError").className = "banner " + friendlyKind(r.data.kind);
    $("#loginError").textContent = friendlyMessage(r.data.kind, r.data.message);
  }
});

$("#logoutBtn").addEventListener("click", () => {
  document.cookie = "nurux_session=; Max-Age=0; path=/";
  enterLogin();
});

// ------------------------------------------------------------- tabs

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    state.mode = tab.dataset.mode;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    $("#chatMode").hidden = state.mode !== "chat";
    $("#researchMode").hidden = state.mode !== "research";
  });
});

// ---------------------------------------------------------- chat

let chatProject = null;

function chatLine(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = text;
  $("#chatLog").appendChild(div);
  $("#chatLog").scrollTop = $("#chatLog").scrollHeight;
}

$("#chatNewProjectBtn").addEventListener("click", async () => {
  clearBanner();
  const r = await api("/api/local/projects", {
    method: "POST",
    body: { title: $("#chatProjectTitle").value || "Chat session", type: "chat" },
  });
  if (!r.ok) { showBanner(r.data.message, r.data.kind); return; }
  chatProject = r.data.project_uuid;
  $("#chatLog").innerHTML = '<div class="msg hint">Chat project ready.</div>';
  chatLine("hint", `Project uuid: ${chatProject}`);
});

$("#chatForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#chatInput").value.trim();
  if (!text) return;
  clearBanner();
  if (!chatProject) {
    const r = await api("/api/local/projects", {
      method: "POST",
      body: { title: "Chat session", type: "chat" },
    });
    if (!r.ok) { showBanner(r.data.message, r.data.kind); return; }
    chatProject = r.data.project_uuid;
    chatLine("hint", `Created chat project: ${chatProject}`);
  }
  chatLine("user", text);
  $("#chatInput").value = "";
  const r = await api("/api/local/chat", {
    method: "POST",
    body: { project_uuid: chatProject, message: text },
  });
  if (!r.ok) { showBanner(r.data.message, r.data.kind); return; }
  chatLine("ai", r.data.reply);
  refreshCredits(r.data.credits_remaining);
});

// -------------------------------------------------------- research

document.querySelectorAll(".res-type-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.resType = btn.dataset.type;
    document.querySelectorAll(".res-type-btn").forEach((b) => b.classList.toggle("active", b === btn));
    $("#resCost").textContent = state.resType === "proposal" ? "~108 credits" : "400–600 credits";
  });
});

function setStep(id, show) { $("#" + id).hidden = !show; }

async function showResStatus() {
  const r = await api("/api/local/me", { method: "GET" });
  if (r.ok) refreshCredits(r.data.credits_balance);
}

$("#createResBtn").addEventListener("click", async () => {
  clearBanner();
  const topic = $("#resTopic").value.trim();
  if (!topic) { showBanner("Enter a topic first.", "http"); return; }
  const r = await api("/api/local/projects", {
    method: "POST",
    body: { title: topic, type: state.resType, auto_title: true },
  });
  if (!r.ok) { showBanner(r.data.message, r.data.kind); return; }
  state.resProjectUuid = r.data.project_uuid;
  state.profile = null;
  $("#resFlow").hidden = false;
  setStep("stepUpload", true); setStep("stepProfile", false); setStep("stepApprove", false);
  setStep("stepOutline", false); setStep("stepGenerate", false); setStep("stepPoll", false);
  setStep("stepDone", false);
  showBanner(`Project created. Upload a source file next — the API needs extracted context before it will build a profile.`, "ok");
  showResStatus();
});

$("#uploadSrcBtn").addEventListener("click", async () => {
  clearBanner();
  const file = $("#sourceFile").files && $("#sourceFile").files[0];
  if (!file) { showBanner("Choose a source file first (PDF/DOCX/TXT/CSV/XLSX).", "http"); return; }
  const fd = new FormData();
  fd.append("file", file);
  fd.append("document_role", $("#sourceRole").value);
  fd.append("type", $("#sourceRole").value);
  const res = await fetch(`/api/local/projects/${state.resProjectUuid}/upload`, {
    method: "POST", body: fd,
  });
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) { showBanner(data.message, data.kind || "http"); return; }
  showBanner("Source uploaded & extracted. Now build the research profile.", "ok");
  setStep("stepUpload", false); setStep("stepProfile", true);
  showResStatus();
});

$("#buildProfileBtn").addEventListener("click", async () => {
  clearBanner();
  setStep("stepProfile", false);
  const r = await api(`/api/local/projects/${state.resProjectUuid}/build-research-profile`, { method: "POST" });
  if (!r.ok) { showBanner(r.data.message, r.data.kind); setStep("stepProfile", true); return; }
  state.profile = r.data.profile || r.data;
  $("#profileJson").value = JSON.stringify(state.profile, null, 2);
  setStep("stepApprove", true);
  showResStatus();
});

$("#approveProfileBtn").addEventListener("click", async () => {
  clearBanner();
  let profile = null;
  try { profile = JSON.parse($("#profileJson").value); }
  catch (_) { showBanner("Profile JSON is invalid — fix it and try again.", "http"); return; }
  const r = await api(`/api/local/projects/${state.resProjectUuid}/approve-research-profile`, {
    method: "POST", body: { research_profile: profile },
  });
  if (!r.ok) { showBanner(r.data.message, r.data.kind); return; }
  setStep("stepApprove", false); setStep("stepOutline", true);
  showResStatus();
});

$("#buildOutlineBtn").addEventListener("click", async () => {
  clearBanner();
  const r = await api(`/api/local/projects/${state.resProjectUuid}/generate-outline`, { method: "POST" });
  if (!r.ok) { showBanner(r.data.message, r.data.kind); return; }
  const outline = r.data.outline || r.data.sections || [];
  $("#outlineBox").textContent = JSON.stringify(outline, null, 1).slice(0, 2000);
  setStep("stepGenerate", true);
  showResStatus();
});

$("#generateBtn").addEventListener("click", async () => {
  clearBanner();
  const type = state.resType;
  const cost = type === "proposal" ? 108 : "400–600";
  if (!confirm(`This queues full ${type} generation and charges ` +
               `${cost} credits up front. Continue?`)) return;
  const r = await api(`/api/local/projects/${state.resProjectUuid}/generate-complete`, {
    method: "POST", body: { type },
  });
  if (!r.ok) { showBanner(r.data.message, r.data.kind); return; }
  setStep("stepGenerate", false); setStep("stepPoll", true);
  state.pollTries = 0;
  pollStatus();
});

async function pollStatus() {
  const r = await api(`/api/local/projects/${state.resProjectUuid}/generation-status`, { method: "GET" });
  if (r.ok && r.data.status !== undefined) {
    renderProgress(r.data);
    if (r.data.status === "completed") { finishDone(); return; }
    if (r.data.status === "failed" || r.data.kind === "generation_failed") {
      showBanner(r.data.message, "generation_failed");
      return;
    }
  }
  state.pollTries += 1;
  if (state.pollTries >= POLL_MAX) {
    showBanner("Generation is taking too long — stopped polling. Refresh to re-check.", "http");
    return;
  }
  state.pollTimer = setTimeout(pollStatus, POLL_MS);
}

function renderProgress(data) {
  const p = Math.round(data.progress || 0);
  $("#progressBar").style.width = p + "%";
  $("#progressLabel").textContent = p + "%";
  $("#currentStep").textContent = data.current_step || "Working...";
  const steps = data.steps || [];
  const list = $("#stepList");
  list.innerHTML = "";
  steps.forEach((s) => {
    const li = document.createElement("li");
    li.textContent = (s.status === "completed" ? "✅ " : s.status === "failed" ? "❌ " : "⏳ ") + (s.message || s.step);
    li.className = s.status === "completed" ? "done" : s.status === "failed" ? "fail" : "active";
    list.appendChild(li);
  });
}

function finishDone() {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  setStep("stepPoll", false); setStep("stepDone", true);
  showResStatus();
}

$("#exportPdfBtn").addEventListener("click", () => doExport("pdf"));
$("#exportWordBtn").addEventListener("click", () => doExport("word"));

async function doExport(fmt) {
  clearBanner();
  const r = await api(`/api/local/projects/${state.resProjectUuid}/export/${fmt}`, { method: "POST" });
  if (!r.ok) { showBanner(r.data.message, r.data.kind); return; }
  window.open(r.data.download_url, "_blank");
  showResStatus();
}

boot();
