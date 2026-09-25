/* ============================================================
   LeaseLens frontend — talks to the FastAPI backend (same origin)
   ============================================================ */
"use strict";

const API = ""; // same origin as the page (served by FastAPI)

const state = {
  report: null,
  docId: null,
  chat: [], // {role, text, sources}
};

const ABSTRACT_LABELS = {
  tenant_name: "Tenant",
  landlord_name: "Landlord",
  property_address: "Property Address",
  lease_commencement_date: "Commencement Date",
  lease_expiration_date: "Expiration Date",
  monthly_base_rent: "Monthly Base Rent",
  annual_rent_escalation: "Annual Rent Escalation",
  security_deposit: "Security Deposit",
  cam_charges_type: "CAM / Lease Type",
  renewal_options: "Renewal Options",
  early_termination_rights: "Early Termination Rights",
  page_citations: "Source Pages",
};

// ---- small helpers ----
const $ = (id) => document.getElementById(id);

// On-page diagnostic log so we can see client-side behavior without DevTools.
function dbg(msg) {
  try { console.log("[LeaseLens]", msg); } catch {}
  const el = document.getElementById("debug");
  if (!el) return;
  const t = new Date().toLocaleTimeString();
  el.innerHTML += `[${t}] ${String(msg)}<br>`;
  el.scrollTop = el.scrollHeight;
  el.style.display = "block";
}
// Surface fatal JS errors on-page instead of failing silently.
window.addEventListener("error", (e) => dbg("JS ERROR: " + e.message));
window.addEventListener("unhandledrejection", (e) =>
  dbg("PROMISE REJECT: " + (e.reason && e.reason.message ? e.reason.message : e.reason)));
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

function money(v) {
  const n = Number(v);
  if (!isFinite(n) || n === 0) return "Not specified";
  return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function displayValue(key, val) {
  if (key === "monthly_base_rent" || key === "security_deposit") return money(val);
  if (key === "page_citations") return (val && val.length) ? val.join(", ") : "—";
  return (val === null || val === undefined || val === "") ? "Not specified" : String(val);
}

let _overlayTimer = null;
function showOverlay(text) {
  const base = text;
  const t0 = Date.now();
  $("overlayText").textContent = base;
  $("overlay").hidden = false;
  clearInterval(_overlayTimer);
  _overlayTimer = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    $("overlayText").textContent = `${base}  (${s}s)`;
  }, 1000);
}
function hideOverlay() {
  clearInterval(_overlayTimer);
  _overlayTimer = null;
  $("overlay").hidden = true;
}

// ---- health ----
async function checkHealth() {
  try {
    const r = await fetch(`${API}/api/health`);
    if (!r.ok) throw new Error();
    const h = await r.json();
    $("statusDot").className = "dot online";
    $("statusText").textContent =
      `Online · ${h.parent_count} sections indexed`;
  } catch {
    $("statusDot").className = "dot offline";
    $("statusText").textContent = "Backend offline";
  }
}

// ---- upload / analyze ----
function setFile(file) {
  state.pendingFile = file || null;
  const btn = $("analyzeBtn");
  if (file) {
    dbg("file selected: " + file.name + " (" + file.size + " bytes, " + (file.type || "no-type") + ")");
    $("dropTitle").textContent = file.name;
    btn.disabled = false;
  } else {
    $("dropTitle").textContent = "Choose a lease PDF";
    btn.disabled = true;
  }
}

async function analyze() {
  const file = state.pendingFile;
  dbg("Run clicked. file = " + (file ? file.name + " (" + file.size + " bytes)" : "NONE"));
  if (!file) { dbg("No file selected — aborting. Pick a PDF first."); return; }
  showOverlay("Parsing, chunking, extracting terms, scanning risks…");
  // Hard safety timeout so a stalled request can never look like an infinite
  // hang. A big lease takes ~1 min; 8 min is a very generous ceiling.
  const controller = new AbortController();
  const timer = setTimeout(() => { dbg("client 8-min timeout hit"); controller.abort(); }, 480000);
  const t0 = Date.now();
  try {
    const fd = new FormData();
    fd.append("file", file, file.name);
    dbg("POST /abstract-lease … (this takes ~50-70s, please wait)");
    const r = await fetch(`${API}/abstract-lease`, {
      method: "POST", body: fd, signal: controller.signal,
    });
    dbg(`response received: HTTP ${r.status} after ${((Date.now()-t0)/1000).toFixed(0)}s`);
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || `Server error ${r.status}`);
    state.report = data;
    state.docId = data.doc_id;
    state.chat = [];
    dbg("rendering dashboard — " + (data.risks ? data.risks.length : 0) + " risks");
    renderDashboard();
    checkHealth();
  } catch (e) {
    const msg = e.name === "AbortError"
      ? "Timed out after 8 minutes — the backend may have stalled."
      : (e.message || String(e));
    dbg("ERROR: " + msg);
    showError("Analysis failed: " + msg);
  } finally {
    clearTimeout(timer);
    hideOverlay();
  }
}

// Inline, always-visible error (browsers can suppress alert()).
function showError(msg) {
  try { alert(msg); } catch {}
  let bar = $("errbar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "errbar";
    bar.className = "errbar";
    document.body.appendChild(bar);
  }
  bar.textContent = "⚠️ " + msg;
  bar.style.display = "block";
  setTimeout(() => { bar.style.display = "none"; }, 12000);
}

// ---- rendering ----
function renderDashboard() {
  const rep = state.report;
  $("hero").hidden = true;
  $("dashboard").hidden = false;
  $("docTitle").textContent = rep.source;
  $("docMeta").textContent =
    `${rep.num_parents} sections · ${rep.num_children} vectors · ${rep.risks.length} risks flagged`;

  renderMetrics(rep.abstract);
  renderTerms(rep.abstract);
  renderRisks(rep.risks);
  renderChat();
  switchTab("abstract");
}

function renderMetrics(a) {
  const cards = [
    ["Monthly Base Rent", money(a.monthly_base_rent)],
    ["Lease Expiration", a.lease_expiration_date || "Not specified"],
    ["CAM / Lease Type", a.cam_charges_type || "Not specified"],
  ];
  $("metrics").innerHTML = cards
    .map(
      ([label, value]) => `
      <div class="metric">
        <div class="metric-label">${esc(label)}</div>
        <div class="metric-value">${esc(value)}</div>
      </div>`
    )
    .join("");
}

function renderTerms(a) {
  const rows = Object.entries(ABSTRACT_LABELS)
    .map(
      ([key, label]) => `
      <tr>
        <td class="k">${esc(label)}</td>
        <td class="v">${esc(displayValue(key, a[key]))}</td>
      </tr>`
    )
    .join("");
  $("termsTable").innerHTML = rows;
}

function renderRisks(risks) {
  const counts = { HIGH: 0, MEDIUM: 0, LOW: 0 };
  risks.forEach((r) => { counts[r.severity] = (counts[r.severity] || 0) + 1; });
  $("riskCounts").innerHTML =
    `<span style="color:var(--high-tx)">🔴 ${counts.HIGH}</span>
     <span style="color:var(--med-tx)">🟡 ${counts.MEDIUM}</span>
     <span style="color:var(--low-tx)">🟢 ${counts.LOW}</span>`;

  const cls = { HIGH: "high", MEDIUM: "med", LOW: "low" };
  if (!risks.length) {
    $("riskList").innerHTML =
      `<p class="empty">No notable tenant-side risks detected. ✅</p>`;
    return;
  }
  $("riskList").innerHTML = risks
    .map((r) => {
      const page = r.page_number ? `page ${r.page_number}` : "page n/a";
      return `
      <div class="risk ${cls[r.severity] || "med"}">
        <div class="risk-top">
          <span class="badge">${esc(r.severity)}</span>
          ${esc(r.issue_title)}
        </div>
        <p class="risk-exp">${esc(r.explanation)}</p>
        <div class="risk-src">📄 ${esc(page)} · §${esc(r.section_clause || "n/a")}</div>
      </div>`;
    })
    .join("");
}

// ---- exports ----
async function exportFile(kind) {
  if (!state.report) return;
  try {
    const r = await fetch(`${API}/export/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.report),
    });
    if (!r.ok) throw new Error("Export failed");
    const blob = await r.blob();
    const disp = r.headers.get("Content-Disposition") || "";
    const match = disp.match(/filename="?([^"]+)"?/);
    const name = match ? match[1] : `lease_report.${kind}`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert("Could not export: " + e.message);
  }
}

// ---- chat ----
function renderChat() {
  const win = $("chatWindow");
  if (!state.chat.length) {
    win.innerHTML =
      `<div class="chat-empty">Ask about rent, termination, CAM, renewal…
       Answers are grounded in the lease and cited to the page.</div>`;
    return;
  }
  win.innerHTML = state.chat
    .map((m) => {
      if (m.role === "user") {
        return `<div class="msg user">${esc(m.text)}</div>`;
      }
      const sources = (m.sources || [])
        .map((s, i) => {
          const pages = (s.pages && s.pages.length) ? s.pages.join(", ") : "?";
          const score = typeof s.score === "number" ? s.score.toFixed(3) : "";
          return `<details class="source">
            <summary>Source ${i + 1} · page ${esc(pages)} · score ${esc(score)}</summary>
            <p>${esc(s.snippet || "")}</p>
          </details>`;
        })
        .join("");
      return `<div class="msg bot">${esc(m.text)}
        ${sources ? `<div class="sources">${sources}</div>` : ""}</div>`;
    })
    .join("");
  win.scrollTop = win.scrollHeight;
}

async function sendChat(e) {
  e.preventDefault();
  const input = $("chatText");
  const text = input.value.trim();
  if (!text || !state.docId) return;
  input.value = "";
  state.chat.push({ role: "user", text });
  renderChat();

  $("sendBtn").disabled = true;
  try {
    const r = await fetch(`${API}/chat-query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: text, doc_id: state.docId }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Query failed");
    state.chat.push({ role: "bot", text: data.answer, sources: data.sources });
  } catch (err) {
    state.chat.push({ role: "bot", text: "⚠️ " + err.message, sources: [] });
  } finally {
    $("sendBtn").disabled = false;
    renderChat();
  }
}

// ---- tabs ----
function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === name)
  );
  $("panel-abstract").hidden = name !== "abstract";
  $("panel-chat").hidden = name !== "chat";
}

function resetToHero() {
  state.report = null; state.docId = null; state.chat = []; state.pendingFile = null;
  $("dashboard").hidden = true;
  $("hero").hidden = false;
  setFile(null);
}

// ---- wire up ----
function init() {
  dbg("app.js v4 loaded — ready.");
  checkHealth();

  const drop = $("drop");
  const fileInput = $("fileInput");
  fileInput.addEventListener("change", () => {
    dbg("file input change event; files=" + fileInput.files.length);
    setFile(fileInput.files[0]);
  });
  ["dragover", "dragenter"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("dragover"); })
  );
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("dragover"); })
  );
  drop.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files[0];
    if (f && f.type === "application/pdf") setFile(f);
  });

  $("analyzeBtn").addEventListener("click", analyze);
  $("newBtn").addEventListener("click", resetToHero);
  $("xlsxBtn").addEventListener("click", () => exportFile("xlsx"));
  $("pdfBtn").addEventListener("click", () => exportFile("pdf"));
  $("chatForm").addEventListener("submit", sendChat);
  document.querySelectorAll(".tab").forEach((t) =>
    t.addEventListener("click", () => switchTab(t.dataset.tab))
  );
}

document.addEventListener("DOMContentLoaded", init);
