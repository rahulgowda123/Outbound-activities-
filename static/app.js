// HubSpot Rep Dashboard frontend.
// Talks to the Flask backend at the same origin.

const state = {
  rows: [],
  totals: null,
  from: null,         // YYYY-MM-DD or null
  to: null,           // YYYY-MM-DD or null
  sortCol: null,
  sortAsc: false,
  view: "current",    // "current" | "old_outbound" | "all_outbound"
  cacheReadyByView: { current: false, old_outbound: false, all_outbound: false },
  reps: [],
  leadSources: [],
  activePreset: null,
};

// ---------- DOM refs ----------
const $ = (sel) => document.querySelector(sel);
const refreshBtn = $("#refreshBtn");
const clearDateBtn = $("#clearDateBtn");
const fromPicker = $("#fromPicker");
const toPicker = $("#toPicker");
const statusBar = $("#statusBar");
const repTbody = $("#repTbody");
const seeOppsBtn = $("#seeOppsBtn");
const drawer = $("#drawer");
const backdrop = $("#backdrop");
const closeDrawerBtn = $("#closeDrawerBtn");
const dealsBody = $("#dealsBody");
const drawerTitle = $("#drawerTitle");
const drawerMeta = $("#drawerMeta");
const overlay = $("#overlay");
const overlayText = $("#overlayText");
const overlaySub = $("#overlaySub");
const cacheMeta = $("#cacheMeta");
const rowMeta = $("#rowMeta");
const showSourcesBtn = $("#showSourcesBtn");
const sourceList = $("#sourceList");

// ---------- Helpers ----------

function fmt(n) {
  if (n == null) return "-";
  return Number(n).toLocaleString();
}

function fmtMoney(v) {
  if (v == null || v === "") return "-";
  const n = Number(v);
  if (!isFinite(n)) return v;
  return "$" + n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function setStatus(text, kind) {
  statusBar.textContent = text || "";
  statusBar.classList.remove("ok", "err");
  if (kind === "ok") statusBar.classList.add("ok");
  else if (kind === "err") statusBar.classList.add("err");
}

function showOverlay(text, sub) {
  overlayText.textContent = text || "Working ...";
  overlaySub.textContent = sub || "";
  overlay.classList.remove("hidden");
}

function hideOverlay() {
  overlay.classList.add("hidden");
}

function updateCacheMeta(statusJson) {
  if (!statusJson || !statusJson.has_token) {
    cacheMeta.textContent = "HUBSPOT_TOKEN missing in .env";
    return;
  }
  const v = statusJson.views && statusJson.views[state.view];
  if (!v || !v.has_cache) {
    cacheMeta.textContent = `No ${viewLabel(state.view)} data yet. Click Refresh.`;
    return;
  }
  const when = new Date(v.fetched_at * 1000);
  const ago = Math.max(0, Math.floor((Date.now() - when.getTime()) / 60000));
  cacheMeta.textContent = `${viewLabel(state.view)} cached ${ago} min ago - ${when.toLocaleString()}`;
}

function viewLabel(v) {
  if (v === "old_outbound") return "Old Outbound";
  if (v === "all_outbound") return "All Outbound";
  return "Current";
}

// ---------- API ----------

async function api(url, opts) {
  const resp = await fetch(url, opts);
  const text = await resp.text();
  let json = null;
  try { json = JSON.parse(text); } catch (_) {}
  if (!resp.ok) {
    const msg = (json && json.error) || text || `HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return json;
}

async function getStatus() {
  return api("/api/status");
}

async function startRefresh() {
  const { job_id } = await api(`/api/refresh?view=${state.view}`, { method: "POST" });
  return job_id;
}

async function pollJob(jobId, onProgress) {
  // If the server process recycles mid-fetch (Render free tier often does
  // this), our job_id becomes "unknown". When that happens, transparently
  // start a fresh refresh and resume polling instead of failing.
  let restarts = 0;
  while (true) {
    let job;
    try {
      job = await api(`/api/job/${jobId}`);
    } catch (e) {
      const msg = String(e.message || "").toLowerCase();
      if (msg.includes("unknown job") && restarts < 3) {
        restarts++;
        if (onProgress) onProgress({status: "running", message: `Server recycled - restarting refresh (${restarts}/3) ...`});
        await new Promise((r) => setTimeout(r, 1500));
        jobId = await startRefresh();
        continue;
      }
      throw e;
    }
    if (onProgress) onProgress(job);
    if (job.status === "done") return job;
    if (job.status === "error") throw new Error(job.error || "Refresh failed");
    await new Promise((r) => setTimeout(r, 1500));
  }
}

function buildQuery(extra = {}) {
  const params = new URLSearchParams();
  params.set("view", state.view);
  if (state.from) params.set("from", state.from);
  if (state.to) params.set("to", state.to);
  for (const [k, v] of Object.entries(extra)) {
    if (v) params.set(k, v);
  }
  return "?" + params.toString();
}

async function getDashboard() {
  return api(`/api/dashboard${buildQuery()}`);
}

async function getOpportunities(ownerId) {
  return api(`/api/opportunities${buildQuery({ owner_id: ownerId })}`);
}

// ---------- Date helpers ----------

function toISO(d) {
  // YYYY-MM-DD in local time
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function presetRange(name) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  let from, to;
  switch (name) {
    case "today":
      from = to = new Date(today);
      break;
    case "yesterday":
      from = new Date(today); from.setDate(today.getDate() - 1);
      to = new Date(from);
      break;
    case "last7":
      to = new Date(today);
      from = new Date(today); from.setDate(today.getDate() - 6);
      break;
    case "last14":
      to = new Date(today);
      from = new Date(today); from.setDate(today.getDate() - 13);
      break;
    case "thismonth":
      from = new Date(today.getFullYear(), today.getMonth(), 1);
      to = new Date(today);
      break;
    case "lastmonth":
      from = new Date(today.getFullYear(), today.getMonth() - 1, 1);
      to = new Date(today.getFullYear(), today.getMonth(), 0); // 0th of this month = last day of prev month
      break;
    default:
      return { from: null, to: null };
  }
  return { from: toISO(from), to: toISO(to) };
}

function rangeLabel() {
  if (!state.from && !state.to) return "All-time activity";
  if (state.from && state.to) {
    return state.from === state.to ? `Activities on ${state.from}` : `Activities from ${state.from} to ${state.to}`;
  }
  if (state.from) return `Activities from ${state.from} onwards`;
  return `Activities up to ${state.to}`;
}

function markActivePreset(name) {
  state.activePreset = name;
  document.querySelectorAll(".chip[data-preset]").forEach((c) => {
    c.classList.toggle("active", c.dataset.preset === name);
  });
}

// ---------- Rendering ----------

function renderRows() {
  const rows = state.rows.slice();
  if (state.sortCol) {
    rows.sort((a, b) => {
      const av = a[state.sortCol], bv = b[state.sortCol];
      if (typeof av === "string") return state.sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
      return state.sortAsc ? av - bv : bv - av;
    });
  }
  if (rows.length === 0) {
    repTbody.innerHTML = `<tr><td colspan="7" class="empty">No data. Click <strong>Refresh data</strong>.</td></tr>`;
    return;
  }
  let html = "";
  for (const r of rows) {
    html += `<tr>
      <td class="left">${r.rep}</td>
      <td>${fmt(r.contacts)}</td>
      <td>${fmt(r.calls)}</td>
      <td>${fmt(r.emails)}</td>
      <td>${fmt(r.connected)}</td>
      <td>${fmt(r.opportunities)}</td>
      <td class="left">
        <button class="btn-link" data-owner="${r.owner_id}" data-rep="${r.rep}" data-opps="${r.opportunities}" ${r.opportunities ? "" : "disabled"}>
          View opps
        </button>
      </td>
    </tr>`;
  }
  if (state.totals) {
    const t = state.totals;
    html += `<tr class="total">
      <td class="left">TOTAL</td>
      <td>${fmt(t.contacts)}</td>
      <td>${fmt(t.calls)}</td>
      <td>${fmt(t.emails)}</td>
      <td>${fmt(t.connected)}</td>
      <td>${fmt(t.opportunities)}</td>
      <td></td>
    </tr>`;
  }
  repTbody.innerHTML = html;
  // wire per-rep "View opps" buttons
  repTbody.querySelectorAll("button[data-owner]").forEach((b) => {
    b.addEventListener("click", () => {
      const owner = b.getAttribute("data-owner");
      const rep = b.getAttribute("data-rep");
      openDrawerForOwner(owner, rep);
    });
  });

  // sort header indicators
  document.querySelectorAll("#repTable thead th").forEach((th) => {
    th.classList.remove("sorted", "asc");
    if (th.dataset.col === state.sortCol) {
      th.classList.add("sorted");
      if (state.sortAsc) th.classList.add("asc");
    }
  });

  // KPI tiles
  if (state.totals) {
    Object.entries(state.totals).forEach(([k, v]) => {
      const el = document.querySelector(`[data-kpi="${k}"]`);
      if (el) el.textContent = fmt(v);
    });
  }
  rowMeta.textContent = rangeLabel();
  seeOppsBtn.disabled = !(state.totals && state.totals.opportunities > 0);
}

function renderSources(sources) {
  sourceList.innerHTML = sources.map((s) => `<span class="src">${s}</span>`).join("");
}

// ---------- Drawer (Opportunities) ----------

function openDrawer() {
  drawer.classList.remove("hidden");
  backdrop.classList.remove("hidden");
}
function closeDrawer() {
  drawer.classList.add("hidden");
  backdrop.classList.add("hidden");
}

async function openDrawerForAll() {
  drawerTitle.textContent = "Opportunities - all reps";
  drawerMeta.textContent = "Loading ...";
  dealsBody.innerHTML = `<tr><td colspan="7" class="empty">Loading ...</td></tr>`;
  openDrawer();
  try {
    const res = await getOpportunities(null);
    renderDeals(res, "all reps");
  } catch (e) {
    drawerMeta.textContent = "Error: " + e.message;
    dealsBody.innerHTML = `<tr><td colspan="7" class="empty">Failed to load.</td></tr>`;
  }
}

async function openDrawerForOwner(ownerId, repName) {
  drawerTitle.textContent = `Opportunities - ${repName}`;
  drawerMeta.textContent = "Loading ...";
  dealsBody.innerHTML = `<tr><td colspan="7" class="empty">Loading ...</td></tr>`;
  openDrawer();
  try {
    const res = await getOpportunities(ownerId);
    renderDeals(res, repName);
  } catch (e) {
    drawerMeta.textContent = "Error: " + e.message;
    dealsBody.innerHTML = `<tr><td colspan="7" class="empty">Failed to load.</td></tr>`;
  }
}

function renderDeals(res, scope) {
  const deals = res.deals || [];
  let scopeDesc;
  if (res.from && res.to) {
    scopeDesc = res.from === res.to ? `created on ${res.from}` : `created between ${res.from} and ${res.to}`;
  } else if (res.from) scopeDesc = `created from ${res.from} onwards`;
  else if (res.to) scopeDesc = `created up to ${res.to}`;
  else scopeDesc = `total`;
  drawerMeta.textContent = `${deals.length} deal${deals.length === 1 ? "" : "s"} ${scopeDesc} - ${scope}`;
  if (!deals.length) {
    const inRange = res.from || res.to ? " in this range" : "";
    dealsBody.innerHTML = `<tr><td colspan="7" class="empty">No opportunities${inRange}.</td></tr>`;
    return;
  }
  dealsBody.innerHTML = deals.map((d) => `
    <tr>
      <td class="left">${escapeHtml(d.dealname || "(no name)")}</td>
      <td class="left">${escapeHtml(d.rep)}</td>
      <td class="left">${renderLeadSourceChips(d.lead_source)}</td>
      <td>${fmtMoney(d.amount)}</td>
      <td class="left">${escapeHtml(d.dealstage || "-")}</td>
      <td class="left">${d.closedate || "-"}</td>
      <td class="left">${d.createdate || "-"}</td>
    </tr>
  `).join("");
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function renderLeadSourceChips(src) {
  if (!src) return '<span class="muted">-</span>';
  return src.split(",").map((s) => s.trim()).filter(Boolean)
    .map((s) => `<span class="pill pill-blue ls-chip">${escapeHtml(s)}</span>`)
    .join(" ");
}

// ---------- Flow ----------

async function loadDashboard() {
  try {
    const data = await getDashboard();
    state.rows = data.rows;
    state.totals = data.totals;
    state.sortCol = "calls";
    state.sortAsc = false;
    renderRows();
    setStatus(`Loaded ${data.rows.length} reps - ${rangeLabel().toLowerCase()}.`, "ok");
  } catch (e) {
    setStatus(e.message, "err");
  }
}

async function doRefresh() {
  refreshBtn.disabled = true;
  setStatus("");
  const sub = state.view === "all_outbound"
    ? "Pulling Current then Old Outbound sequentially. Expect 4-8 minutes."
    : "This first run can take 1-3 minutes.";
  showOverlay(`Pulling ${viewLabel(state.view)} data from HubSpot ...`, sub);
  try {
    const jobId = await startRefresh();
    await pollJob(jobId, (j) => {
      overlaySub.textContent = j.message || "";
    });
    hideOverlay();
    const info = await getStatus();
    // hydrate flags from server status (handles both primary refreshes done by All Outbound)
    for (const v of Object.keys(state.cacheReadyByView)) {
      state.cacheReadyByView[v] = !!(info.views && info.views[v] && info.views[v].has_cache);
    }
    updateCacheMeta(info);
    await loadDashboard();
  } catch (e) {
    hideOverlay();
    setStatus(e.message, "err");
  } finally {
    refreshBtn.disabled = false;
  }
}

// ---------- Wire events ----------

refreshBtn.addEventListener("click", doRefresh);

async function onRangeChanged({ silent } = {}) {
  // sync state from the two pickers
  state.from = fromPicker.value || null;
  state.to = toPicker.value || null;
  // simple sanity: if both set and from > to, swap
  if (state.from && state.to && state.from > state.to) {
    [state.from, state.to] = [state.to, state.from];
    fromPicker.value = state.from;
    toPicker.value = state.to;
  }
  if (!silent) markActivePreset(null);
  if (state.cacheReadyByView[state.view]) await loadDashboard();
}

fromPicker.addEventListener("change", () => onRangeChanged());
toPicker.addEventListener("change", () => onRangeChanged());

clearDateBtn.addEventListener("click", async () => {
  fromPicker.value = "";
  toPicker.value = "";
  state.from = null;
  state.to = null;
  markActivePreset(null);
  if (state.cacheReadyByView[state.view]) await loadDashboard();
});

document.querySelectorAll(".chip[data-preset]").forEach((chip) => {
  chip.addEventListener("click", async () => {
    const { from, to } = presetRange(chip.dataset.preset);
    fromPicker.value = from || "";
    toPicker.value = to || "";
    state.from = from;
    state.to = to;
    markActivePreset(chip.dataset.preset);
    if (state.cacheReadyByView[state.view]) await loadDashboard();
  });
});

document.querySelectorAll("#repTable thead th[data-col]").forEach((th) => {
  th.addEventListener("click", () => {
    const col = th.dataset.col;
    if (state.sortCol === col) state.sortAsc = !state.sortAsc;
    else { state.sortCol = col; state.sortAsc = col === "rep"; }
    renderRows();
  });
});

seeOppsBtn.addEventListener("click", openDrawerForAll);
closeDrawerBtn.addEventListener("click", closeDrawer);
backdrop.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !drawer.classList.contains("hidden")) closeDrawer();
});

showSourcesBtn.addEventListener("click", () => {
  sourceList.classList.toggle("hidden");
  showSourcesBtn.textContent = sourceList.classList.contains("hidden") ? "Show sources" : "Hide sources";
});

// View tab switching
document.querySelectorAll(".view-tab[data-view]").forEach((tab) => {
  tab.addEventListener("click", async () => {
    const newView = tab.dataset.view;
    if (newView === state.view) return;
    state.view = newView;
    document.querySelectorAll(".view-tab").forEach((t) => t.classList.toggle("active", t === tab));
    updateLeadSourcePill();
    // refresh status meta
    try {
      const info = await getStatus();
      updateCacheMeta(info);
    } catch (_) {}
    if (state.cacheReadyByView[state.view]) {
      await loadDashboard();
    } else {
      // wipe rows so the user knows this view needs a refresh
      state.rows = [];
      state.totals = null;
      renderRows();
      if (state.view === "all_outbound") {
        setStatus(`All Outbound needs both Current and Old Outbound cached. Click Refresh data to pull both.`);
      } else {
        setStatus(`No data cached for ${viewLabel(state.view)}. Click Refresh data to pull it.`);
      }
    }
  });
});

function updateLeadSourcePill() {
  const pill = document.getElementById("leadSourcePill");
  if (!pill) return;
  const year = new Date().getFullYear();
  if (state.view === "old_outbound") {
    pill.innerHTML = `Lead Source &notin; 21 sources &middot; createdate &lt; Jan 1, ${year}`;
  } else if (state.view === "all_outbound") {
    pill.innerHTML = `(Current &cup; Old Outbound) &middot; ${state.leadSources.length} sources + pre-${year} legacy`;
  } else {
    pill.innerHTML = `Lead Source &isin; ${state.leadSources.length} sources`;
  }
  // also keep tab subtitle year in sync
  const yEl = document.getElementById("ooYear");
  if (yEl) yEl.textContent = year;
}

// ---------- Boot ----------

(async function init() {
  try {
    const s = await getStatus();
    state.reps = s.reps;
    state.leadSources = s.lead_sources;
    renderSources(s.lead_sources);
    updateLeadSourcePill();
    updateCacheMeta(s);
    // hydrate cache-ready flags from server status
    for (const v of Object.keys(state.cacheReadyByView)) {
      state.cacheReadyByView[v] = !!(s.views && s.views[v] && s.views[v].has_cache);
    }
    if (!s.has_token) {
      setStatus("HUBSPOT_TOKEN missing in .env. Add it next to app.py and restart.", "err");
      refreshBtn.disabled = true;
    } else if (state.cacheReadyByView[state.view]) {
      await loadDashboard();
    } else {
      setStatus(`Ready. Click Refresh data to pull ${viewLabel(state.view)} data from HubSpot.`);
    }
  } catch (e) {
    setStatus("Failed to reach backend: " + e.message, "err");
  }
})();
