/* Petcare MMM Studio — client logic (v2: Meridian + EDA workspace) */
'use strict';

/* ============================== helpers ============================== */
const PALETTE = document.body.dataset.palette.split(',');
const INK = '#16163F', INK_SOFT = '#4A4A6A', GRIDC = '#ECECF4', BASELINE_GRAY = '#C9C9D9';
const $ = id => document.getElementById(id);
const fmt = n => (n == null || !isFinite(n)) ? '—' : Intl.NumberFormat('en-GB', {maximumFractionDigits: 0}).format(n);
const fmt2 = n => (n == null || !isFinite(n)) ? '—' : Intl.NumberFormat('en-GB', {maximumFractionDigits: 2}).format(n);
const pct = n => (n == null || !isFinite(n)) ? '—' : (n * 100).toFixed(1) + '%';
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nice = c => String(c).replace(/_/g, ' ').replace(/\b(spend|impressions?)\b/gi, '').trim()
                  .replace(/\b\w/g, m => m.toUpperCase()) || c;
const color = i => PALETTE[i % 8];

let toastT;
function toast(msg, err = false) {
  const t = $('toast');
  t.textContent = msg; t.className = 'show' + (err ? ' err' : '');
  clearTimeout(toastT); toastT = setTimeout(() => t.className = '', 4500);
}
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}
const post = (path, data) => api(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                        body: JSON.stringify(data)});

Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
Chart.defaults.font.size = 12;
Chart.defaults.color = INK_SOFT;
Chart.defaults.borderColor = GRIDC;
Chart.defaults.plugins.legend.labels.boxWidth = 10;
Chart.defaults.plugins.legend.labels.boxHeight = 10;
Chart.defaults.plugins.legend.labels.usePointStyle = true;
Chart.defaults.plugins.legend.labels.pointStyle = 'rectRounded';
Chart.defaults.plugins.tooltip.backgroundColor = INK;
Chart.defaults.plugins.tooltip.titleFont = {weight: '700'};
Chart.defaults.plugins.tooltip.padding = 10;
Chart.defaults.plugins.tooltip.cornerRadius = 8;
Chart.defaults.animation.duration = 300;
const charts = {};
function draw(id, cfg) {
  const el = $(id);
  if (!el) return;
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(el, cfg);
}

/* ============================== state ============================== */
let DS = null;          // dataset summary payload (incl. meridian_mapping)
let RES = null;         // model results payload
let ENGINE = 'meridian';
let MERIDIAN = {available: false};
let selectedFile = null;
const EDA = {active: 'spend_media', cache: {}, params: {}};
let PRIORS = {};        // {channel: {mu, sigma}}
let pollTimer = null;

/* ============================== tabs ============================== */
document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => {
  if (b.disabled) return;
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x === b));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + b.dataset.view));
  if (b.dataset.view === 'eda') showEda(EDA.active);
}));
const gotoTab = v => document.querySelector(`.tab[data-view=${v}]`).click();

/* ============================== data tab ============================== */
async function refreshFiles() {
  try {
    const d = await api('/api/files');
    $('dataDirNote').textContent = 'Files are read from ' + d.data_dir + ' — drop your Excel there or upload it here.';
    const box = $('files'); box.innerHTML = '';
    if (!d.files.length) box.innerHTML = '<div class="note">No Excel files in the data folder yet.</div>';
    d.files.forEach(f => {
      const row = document.createElement('div');
      row.className = 'filerow' + (f === selectedFile ? ' sel' : '');
      row.innerHTML = `<span style="font-size:18px">📊</span><span class="nm">${esc(f)}</span>
                       <button class="btn ghost sm" style="margin-left:auto;">Load</button>`;
      row.querySelector('button').onclick = e => { e.stopPropagation(); loadFile(f); };
      row.onclick = () => loadFile(f);
      box.appendChild(row);
    });
  } catch (e) { toast('Cannot reach the local API: ' + e.message, true); }
}

async function loadFile(name) {
  try {
    toast('Loading ' + name + ' …');
    const d = await post('/api/load', {filename: name});
    selectedFile = name; onLoaded(d);
  } catch (e) { toast(e.message, true); }
}
$('fileInput').addEventListener('change', async e => { if (e.target.files[0]) await uploadFile(e.target.files[0]); });
const drop = $('drop');
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', async e => { const f = e.dataTransfer.files[0]; if (f) await uploadFile(f); });
async function uploadFile(f) {
  try {
    toast('Uploading ' + f.name + ' …');
    const fd = new FormData(); fd.append('file', f);
    const d = await api('/api/upload', {method: 'POST', body: fd});
    selectedFile = d.file; onLoaded(d); refreshFiles();
  } catch (e) { toast(e.message, true); }
}

function mm() { return DS ? DS.meridian_mapping : null; }

function onLoaded(d) {
  DS = d; RES = null; EDA.cache = {}; PRIORS = {};
  MERIDIAN = d.meridian || {available: false};
  $('tabEda').disabled = false; $('tabModel').disabled = false;
  $('tabResults').disabled = true; $('tabOpt').disabled = true;
  $('engineBadge').textContent = MERIDIAN.available
      ? `Meridian ${MERIDIAN.version} ready · data stays on this machine`
      : 'Meridian not installed — classic engine only';

  const m = d.meridian_mapping;
  $('dsMeta').innerHTML = `<b>${esc(d.file)}</b> — ${d.rows} rows · ${d.frequency || '?'} · ${d.date_min} → ${d.date_max}` +
    (m.geo_col ? ` · <b>${DS.preview ? '' : ''}${m.format === 'meridian' ? 'geo-level' : ''}</b>` : ' · national');
  const chips = $('dsChips'); chips.innerHTML = '';
  const add = (txt, on) => { const c = document.createElement('span'); c.className = 'chip static' + (on ? ' on' : ''); c.textContent = txt; chips.appendChild(c); };
  add(m.format === 'long' ? 'campaign-grain (long) format'
      : (m.format === 'meridian' ? 'Meridian format' : 'simple format'), true);
  if (d.is_long) {
    add(`${d.long_rows.toLocaleString()} campaign rows → ${d.rows} ${m.geo_col} × week`, true);
    add(`${(m.filter_dimensions || []).length} multi-select filters`, true);
    (m.primary_filters || []).forEach(dim =>
      add(`${dim} (${(d.facets?.[dim] || []).length})`, false));
  }
  if (m.geo_col) add(`geo/brand: ${m.geo_col}`, true);
  if (m.population_col) add(`population: ${m.population_col}`, false);
  add(`KPI: ${m.kpi} (${m.kpi_type})`, true);
  if (m.revenue_per_kpi) add(`rev/KPI: ${m.revenue_per_kpi}`, false);
  m.channels.forEach(c => add(`media: ${c.name}${c.units_col ? ' (units+spend)' : ' (spend)'}`, false));
  m.organic.forEach(c => add(`organic: ${c}`, false));
  m.non_media.forEach(c => add(`treatment: ${c}`, false));
  m.controls.forEach(c => add(`control: ${c}`, false));

  const cols = d.columns;
  $('preview').innerHTML = '<table class="data"><thead><tr>' + cols.map(c => `<th>${esc(c)}</th>`).join('') +
    '</tr></thead><tbody>' + d.preview.map(r => '<tr>' + cols.map(c => `<td>${esc(r[c] ?? '')}</td>`).join('') + '</tr>').join('') +
    '</tbody></table>';

  renderLongBox(d);
  buildModelForm();
  document.querySelectorAll('.filerow').forEach(r => r.classList.toggle('sel', r.querySelector('.nm')?.textContent === selectedFile));
  toast('Loaded — mapping detected. EDA reports are ready to generate.');
  gotoTab('eda');
}

/* Global KPI choice + dataset-wide filters for long-format files. These set the
   baseline every report starts from; each report can narrow further. */
function renderLongBox(d) {
  const box = $('longBox');
  if (!box) return;
  if (!d.is_long) { box.style.display = 'none'; box.innerHTML = ''; return; }
  const facets = d.facets || {}, cur = d.filters || {};
  const prim = d.meridian_mapping.primary_filters || [];
  const dims = (d.meridian_mapping.filter_dimensions || []).filter(x => (facets[x] || []).length);
  const kpiOpts = (d.kpi_options || []).map(k =>
    `<option value="${esc(k)}" ${k === d.kpi_choice ? 'selected' : ''}>${esc(k)}</option>`).join('');
  const lead = dims.filter(x => prim.includes(x));
  const rest = dims.filter(x => !prim.includes(x));
  const nExtra = Object.keys(cur).filter(k => !prim.includes(k)).length;

  box.style.display = '';
  box.innerHTML = `<div class="params" id="globalFilters">
      <div class="p"><label>KPI to model</label><select id="kpiChoice">${kpiOpts}</select></div>
      ${lead.map(x => msHtml(x, facets[x], cur[x], 'gf')).join('')}
      ${rest.length ? `<div class="p" style="min-width:auto;"><label>&nbsp;</label>
        <button type="button" class="btn ghost sm" data-more="global">
          More filters${nExtra ? ` (${nExtra})` : ''} ▾</button></div>` : ''}
      <button class="btn sm" id="btnApplyFilters">Apply</button>
      ${rest.length ? `<div id="more-global" style="display:${nExtra ? 'flex' : 'none'}; gap:12px;
        flex-wrap:wrap; align-items:flex-end; width:100%; border-top:1px solid var(--border);
        padding-top:10px;">${rest.map(x => msHtml(x, facets[x], cur[x], 'gf')).join('')}</div>` : ''}
    </div>
    <div class="note" style="margin:-6px 0 12px;">Tick any combination — brand × sub-brand × retailer
      and so on. Filters re-aggregate the campaign rows, they don't just re-cut the panel.
      CLICKS as the KPI models engagement: the click columns leave the media side so the model
      never predicts an input from itself.</div>`;
  $('btnApplyFilters').addEventListener('click', applyGlobalFilters);
}

async function applyGlobalFilters() {
  const filters = msCollect($('globalFilters'));
  const kpi = $('kpiChoice').value;
  try {
    const d = await post('/api/filters', {kpi, filters});
    DS = {...DS, ...d, is_long: true};
    EDA.cache = {}; EDA.params = {}; PRIORS = {}; RES = null;
    $('tabResults').disabled = true; $('tabOpt').disabled = true;
    renderLongBox(DS);
    buildModelForm();
    toast(`Panel rebuilt — KPI ${kpi}, ${d.rows} rows.`);
    if (EDA.active) showEda(EDA.active);
  } catch (e) { toast(e.message, true); }
}

/* ============================== mapping / model form ============================== */
function chipSet(boxId, items, onList, colorize) {
  const box = $(boxId); box.innerHTML = '';
  items.forEach((c, i) => {
    const el = document.createElement('span');
    el.className = 'chip' + (onList.includes(c) ? ' on' : '');
    el.dataset.col = c;
    el.innerHTML = (colorize ? `<span class="sw" style="background:${color(i)}"></span>` : '') + esc(c);
    el.onclick = () => el.classList.toggle('on');
    box.appendChild(el);
  });
  if (!items.length) box.innerHTML = '<span class="note">none detected</span>';
}
const chipGet = boxId => [...$(boxId).querySelectorAll('.chip.on')].map(c => c.dataset.col);

function buildModelForm() {
  const m = mm();
  const numeric = DS.columns.filter(c => ![m.time_col, m.geo_col].includes(c));
  $('selKpi').innerHTML = numeric.map(c => `<option ${c === m.kpi ? 'selected' : ''}>${esc(c)}</option>`).join('');
  $('selKpiType').value = m.kpi_type || 'revenue';
  $('selRpk').innerHTML = '<option value="">—</option>' +
    numeric.map(c => `<option ${c === m.revenue_per_kpi ? 'selected' : ''}>${esc(c)}</option>`).join('');
  chipSet('chipChannels', m.channels.map(c => c.name), m.channels.map(c => c.name), true);
  chipSet('chipOrganic', m.organic, m.organic);
  chipSet('chipNonMedia', m.non_media, m.non_media);
  chipSet('chipControls', m.controls, m.controls);
  setEngine(MERIDIAN.available ? 'meridian' : 'classic');
  $('modelSub').textContent = MERIDIAN.available
    ? 'Meridian runs full Bayesian MCMC — minutes, with credible intervals. The classic engine gives an instant approximate read.'
    : 'Meridian is not installed in this Python environment (pip install google-meridian). Using the classic engine.';
}

document.querySelectorAll('#engineChips .chip').forEach(ch => ch.addEventListener('click', () => setEngine(ch.dataset.eng)));
function setEngine(eng) {
  if (eng === 'meridian' && !MERIDIAN.available) { toast('Meridian not installed — pip install google-meridian', true); eng = 'classic'; }
  ENGINE = eng;
  document.querySelectorAll('#engineChips .chip').forEach(c => c.classList.toggle('on', c.dataset.eng === eng));
  $('meridianParams').style.display = eng === 'meridian' ? '' : 'none';
}

async function applyMapping() {
  const m = mm();
  const chosen = chipGet('chipChannels');
  const body = {
    kpi: $('selKpi').value,
    kpi_type: $('selKpiType').value,
    revenue_per_kpi: $('selRpk').value || null,
    channels: m.channels.filter(c => chosen.includes(c.name)),
    organic: chipGet('chipOrganic'),
    non_media: chipGet('chipNonMedia'),
    controls: chipGet('chipControls'),
  };
  const d = await post('/api/mapping', body);
  DS.meridian_mapping = d.meridian_mapping;
  return d.meridian_mapping;
}

/* ============================== EDA ============================== */
document.querySelectorAll('#edaTabs .subtab').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('#edaTabs .subtab').forEach(x => x.classList.toggle('active', x === b));
  showEda(b.dataset.eda);
}));

const EDA_DEFAULT_PARAMS = {
  spend_media: {iqr_k: 1.5, small_share_pct: 2.0, ratio_warn: 15},
  variables: {iqr_k: 1.5, std_threshold: 1e-4, scale_population: true},
  population: {high_corr: 0.6},
  relationships: {corr_attention: 0.90, corr_error: 0.999, vif_threshold: 1000},
  priors: {neg_attention: 0.2, neg_error: 0.5},
  complete: {},
};

async function showEda(section) {
  EDA.active = section;
  if (!DS) { $('edaBody').innerHTML = '<div class="card"><div class="sub">Load a dataset first.</div></div>'; return; }
  if (!EDA.cache[section]) await runEda(section);
  else renderEda(section);
}

async function runEda(section) {
  $('edaBody').innerHTML = `<div class="card"><div class="sub"><span class="spin dark"></span>&nbsp; Generating the ${section.replace('_', ' ')} report…</div></div>`;
  try {
    const params = EDA.params[section] || EDA_DEFAULT_PARAMS[section];
    const out = await post('/api/eda', {section, params});
    EDA.cache[section] = out;
    renderEda(section);
  } catch (e) {
    $('edaBody').innerHTML = `<div class="banner red">EDA failed: ${esc(e.message)}</div>`;
  }
}

function badge(s) { return `<span class="badge ${s}">${s}</span>`; }
function aiCard(text) {
  return `<div class="aicard"><div class="hd"><span class="spark"></span>AI summary — generated locally from your data</div><p>${esc(text)}</p></div>`;
}
function findingsList(fs) {
  if (!fs || !fs.length) return '<div class="findings"><div class="finding">' + badge('OK') + '<span>No issues flagged in this category.</span></div></div>';
  return '<div class="findings">' + fs.map(f => `<div class="finding">${badge(f.severity)}<span>${esc(f.text)}</span></div>`).join('') + '</div>';
}
/* Per-report filters. Long-format datasets expose every campaign-grain dimension
   as a MULTI-SELECT filter on every report — the server re-aggregates the raw
   rows for the slice rather than re-cutting the panel, so combinations like
   brand × sub-brand × retailer are all real re-aggregations. */
const FILTER_LABELS = {
  BRAND: 'Brand', SUB_BRAND: 'Sub-brand', MARKETING_CHANNEL: 'Marketing channel',
  RETAILER_NAME: 'Retailer', RETAIL_CHANNEL: 'Retail channel', CHANNEL_NAME: 'Channel name',
  MARKETING_TYPE: 'Marketing type', PRODUCT_FAMILY: 'Product family', SPECIES: 'Species',
  BRAND_TECH: 'Brand tech', SKU_NAME: 'SKU', CAMPAIGN_NAME: 'Campaign',
  CAMPAIGN_OBJECTIVE: 'Campaign objective', MEDIA_OBJECTIVE: 'Media objective',
  PLATFORM_PLACEMENT: 'Platform / placement', FORMAT_CAMPAIGN_TYPE: 'Format / type',
  DURATION_LENGTH_SIZE: 'Duration / size', AUDIENCE_NAME_AUDIENCE_TYPE: 'Audience',
  CREATIVE_NAME_DEVICE_NAME: 'Creative / device', BUY_TYPE_LANGUAGE: 'Buy type / language',
  ZONE: 'Zone', REGION: 'Region', COUNTRY: 'Country', COUNTRY_REGION: 'Country region',
  PROVINCE: 'Province', LOCATION: 'Location', MANUFACTURER: 'Manufacturer',
};
const flabel = d => FILTER_LABELS[d] || d.replace(/_/g, ' ').toLowerCase();

/* Dimensions shown up-front on a report; the rest sit behind "More filters". */
function primaryDims() { return DS?.meridian_mapping?.primary_filters || []; }
function allDims() { return DS?.meridian_mapping?.filter_dimensions || []; }

function filterFields() {
  if (!DS || !DS.is_long) return [];
  const facets = DS.facets || {};
  return allDims().filter(d => (facets[d] || []).length)
    .map(d => ({key: d, label: flabel(d), type: 'filter', options: facets[d]}));
}

/* ---- multi-select checkbox dropdown ----
   `sel` is an array of chosen values; empty array == no constraint (All). */
const MS_RENDER_CAP = 300;   // rows drawn up-front; search reaches the rest

function msRows(options, chosen, limit) {
  return options.slice(0, limit).map(o =>
    `<label><input type="checkbox" value="${esc(o)}" ${chosen.has(o) ? 'checked' : ''}>${esc(o)}</label>`
  ).join('');
}

function msHtml(dim, options, sel, attr) {
  const chosen = new Set(sel || []);
  const label = chosen.size === 0 ? 'All'
    : chosen.size === 1 ? [...chosen][0]
    : `${chosen.size} selected`;
  // Always render the chosen values, so a selection outside the first page is
  // never silently dropped when the box is re-read.
  const head = [...chosen].filter(o => options.indexOf(o) >= MS_RENDER_CAP);
  const rows = msRows(head, chosen, head.length) +
               msRows(options, chosen, MS_RENDER_CAP);
  const hidden = Math.max(0, options.length - MS_RENDER_CAP - head.length);
  return `<div class="p"><label>${esc(flabel(dim))}</label>
    <div class="ms" data-ms="${esc(dim)}" data-attr="${attr}">
      <button type="button" class="ms-btn ${chosen.size ? 'on' : ''}" title="${esc(label)}">${esc(label)}</button>
      <div class="ms-pop">
        <div class="ms-tools"><button type="button" data-act="all">Select all</button>
          <button type="button" data-act="none">Clear</button></div>
        ${options.length > 12 ? `<input type="text" class="ms-search" placeholder="Search ${options.length} values…">` : ''}
        <div class="ms-list" data-all="${esc(JSON.stringify(options))}">${rows}</div>
        ${hidden ? `<div class="ms-more">${hidden} more — type to search</div>` : ''}
      </div>
    </div></div>`;
}

/* Read every multi-select inside a container into a {dim: [values]} object. */
function msCollect(root) {
  const out = {};
  root.querySelectorAll('.ms[data-ms]').forEach(ms => {
    const vals = [...ms.querySelectorAll('.ms-list input:checked')].map(i => i.value);
    if (vals.length) out[ms.dataset.ms] = vals;
  });
  return out;
}

function msSyncLabel(ms) {
  const vals = [...ms.querySelectorAll('.ms-list input:checked')].map(i => i.value);
  const btn = ms.querySelector('.ms-btn');
  btn.textContent = vals.length === 0 ? 'All' : vals.length === 1 ? vals[0] : `${vals.length} selected`;
  btn.title = vals.length ? vals.join(', ') : 'All';
  btn.classList.toggle('on', vals.length > 0);
}

/* One delegated listener handles every dropdown, present and future. */
document.addEventListener('click', e => {
  const btn = e.target.closest('.ms-btn');
  const ms = e.target.closest('.ms');
  if (btn && ms) {
    const wasOpen = ms.classList.contains('open');
    document.querySelectorAll('.ms.open').forEach(o => o.classList.remove('open'));
    ms.classList.toggle('open', !wasOpen);
    if (!wasOpen) ms.querySelector('.ms-search')?.focus();
    return;
  }
  if (ms) {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act) {
      // "Select all" respects an active search filter.
      ms.querySelectorAll('.ms-list label').forEach(l => {
        if (l.style.display !== 'none') l.querySelector('input').checked = (act === 'all');
      });
      msSyncLabel(ms);
    }
    return;  // clicks inside the popup keep it open
  }
  document.querySelectorAll('.ms.open').forEach(o => o.classList.remove('open'));
});

document.addEventListener('change', e => {
  const ms = e.target.closest('.ms');
  if (ms && e.target.matches('.ms-list input')) msSyncLabel(ms);
});

document.addEventListener('input', e => {
  if (!e.target.matches('.ms-search')) return;
  const ms = e.target.closest('.ms');
  const list = ms.querySelector('.ms-list');
  const q = e.target.value.trim().toLowerCase();
  // Keep whatever is ticked, then re-draw matches from the FULL option list so
  // a search reaches values beyond the initially rendered page.
  const chosen = new Set([...list.querySelectorAll('input:checked')].map(i => i.value));
  let all = [];
  try { all = JSON.parse(list.dataset.all || '[]'); } catch { all = []; }
  const hits = q ? all.filter(o => o.toLowerCase().includes(q)) : all;
  const show = [...new Set([...chosen, ...hits])].slice(0, MS_RENDER_CAP);
  list.innerHTML = msRows(show, chosen, show.length);
  const more = ms.querySelector('.ms-more');
  if (more) {
    const hidden = Math.max(0, hits.length - show.length);
    more.textContent = hidden ? `${hidden} more — refine your search` : '';
    more.style.display = hidden ? '' : 'none';
  }
});

function paramBox(section, fields) {
  const p = EDA.params[section] || {...EDA_DEFAULT_PARAMS[section]};
  EDA.params[section] = p;
  p.filters = p.filters || {};

  const knobs = fields.map(f => {
    if (f.type === 'check')
      return `<div class="p"><label>${f.label}</label><select data-p="${f.key}"><option value="true" ${p[f.key] ? 'selected' : ''}>yes</option><option value="false" ${!p[f.key] ? 'selected' : ''}>no</option></select></div>`;
    return `<div class="p"><label>${f.label}</label><input type="number" step="${f.step || 'any'}" value="${p[f.key]}" data-p="${f.key}"></div>`;
  }).join('');

  const ff = filterFields();
  let filterHtml = '';
  if (ff.length) {
    const prim = primaryDims();
    const lead = ff.filter(f => prim.includes(f.key));
    const rest = ff.filter(f => !prim.includes(f.key));
    const nExtra = Object.keys(p.filters).filter(k => !prim.includes(k)).length;
    filterHtml =
      lead.map(f => msHtml(f.key, f.options, p.filters[f.key], 'f')).join('') +
      (rest.length ? `<div class="p" style="min-width:auto;">
          <label>&nbsp;</label>
          <button type="button" class="btn ghost sm" data-more="${section}">
            More filters${nExtra ? ` (${nExtra})` : ''} ▾</button></div>` : '') +
      (rest.length ? `<div id="more-${section}" style="display:${nExtra ? 'flex' : 'none'};
          gap:12px; flex-wrap:wrap; align-items:flex-end; width:100%;
          border-top:1px solid var(--border); padding-top:10px; margin-top:2px;">
          ${rest.map(f => msHtml(f.key, f.options, p.filters[f.key], 'f')).join('')}</div>` : '');
  }

  return `<div class="params" id="params-${section}">${filterHtml}${knobs}
    <button class="btn sm" onclick="recalcEda('${section}')">Recalculate</button></div>`;
}

/* "More filters" toggle — delegated so it survives every re-render. */
document.addEventListener('click', e => {
  const t = e.target.closest('[data-more]');
  if (!t) return;
  const box = document.getElementById('more-' + t.dataset.more);
  if (box) box.style.display = box.style.display === 'none' ? 'flex' : 'none';
});

window.recalcEda = async section => {
  const box = document.getElementById('params-' + section);
  const p = EDA.params[section];
  box.querySelectorAll('[data-p]').forEach(el => {
    p[el.dataset.p] = el.tagName === 'SELECT' ? el.value === 'true' : parseFloat(el.value);
  });
  p.filters = msCollect(box);
  EDA.cache[section] = null;
  await runEda(section);
};

function renderEda(section) {
  const out = EDA.cache[section];
  const R = {
    spend_media: renderSpendMedia, variables: renderVariables, population: renderPopulation,
    relationships: renderRelationships, priors: renderPriors, complete: renderComplete,
  }[section];
  R(out);
}

/* ---------- EDA 1: spend & media units ---------- */
function renderSpendMedia(out) {
  const chans = out.spend_share.map(s => s.channel);
  const dp = out.data_to_param;
  $('edaBody').innerHTML = `
    ${paramBox('spend_media', [
      {key: 'iqr_k', label: 'IQR multiplier k', step: 0.1},
      {key: 'small_share_pct', label: 'Small-share line %', step: 0.5},
      {key: 'ratio_warn', label: 'Data/param warn <', step: 1},
    ])}
    <div class="tiles">
      <div class="tile"><div class="k">Paid channels</div><div class="v">${out.table.length}</div></div>
      <div class="tile"><div class="k">Data points</div><div class="v">${fmt(dp.n_geos * dp.n_times)}</div></div>
      <div class="tile"><div class="k">Model parameters (≈)</div><div class="v">${fmt(dp.n_params)}</div></div>
      <div class="tile ${dp.status === 'OK' ? 'accent' : ''}"><div class="k">Data-to-parameter ratio</div>
        <div class="v" style="${dp.status !== 'OK' ? 'color:var(--warn)' : ''}">${dp.ratio}</div></div>
    </div>
    <div class="grid cols2">
      <div class="card"><h2>Share of total spend</h2>
        <div class="sub">Channels under the ${EDA.params.spend_media.small_share_pct}% line are hard to estimate — consider combining.</div>
        <div class="chartbox short"><canvas id="edaShare"></canvas></div></div>
      <div class="card"><h2>Channel health</h2>
        <div class="sub">Cost-per-1k-unit outliers (IQR rule) and spend↔units mismatches, at national level.</div>
        <div class="tblwrap"><table class="data"><thead><tr><th>Channel</th><th class="num">Spend</th>
        <th class="num">Share</th><th class="num">Avg cost/1k</th><th class="num">CPU outlier wks</th>
        <th class="num">Mismatch wks</th><th>Status</th></tr></thead><tbody>
        ${out.table.slice().reverse().map(r => `<tr><td>${esc(r.channel)}</td><td class="num">${fmt(r.total_spend)}</td>
          <td class="num">${r.spend_share_pct.toFixed(1)}%</td><td class="num">${fmt2(r.avg_cost_per_1k_units)}</td>
          <td class="num">${r.cpu_outlier_weeks}</td><td class="num">${r.mismatch_weeks}</td><td>${badge(r.status)}</td></tr>`).join('')}
        </tbody></table></div></div>
    </div>
    <div class="card" style="margin-top:18px;"><h2>Spend vs delivered media units over time</h2>
      <div class="sub">Pick a channel — spend and units should move together; the cost line exposes rate changes and data errors.</div>
      <div class="params"><div class="p"><label>Channel</label><select id="smChan">${chans.map(c => `<option>${esc(c)}</option>`).join('')}</select></div></div>
      <div class="grid cols2">
        <div><div class="chartbox mini"><canvas id="edaSpendTs"></canvas></div></div>
        <div><div class="chartbox mini"><canvas id="edaUnitsTs"></canvas></div></div>
      </div>
      <div class="chartbox mini"><canvas id="edaCpuTs"></canvas></div>
    </div>
    ${findingsList(out.findings)}
    ${aiCard(out.ai_summary)}`;

  draw('edaShare', {type: 'bar', data: {labels: out.spend_share.map(s => nice(s.channel)),
    datasets: [{data: out.spend_share.map(s => s.share_pct), backgroundColor: color(0),
                borderColor: '#fff', borderWidth: 2, borderRadius: 4, maxBarThickness: 30}]},
    options: {indexAxis: 'y', maintainAspectRatio: false, plugins: {legend: {display: false},
      tooltip: {callbacks: {label: c => ` ${c.parsed.x.toFixed(1)}% of spend`}}},
      scales: {x: {ticks: {callback: v => v + '%'}, grid: {color: GRIDC}}, y: {grid: {display: false}}}}});

  const drawTs = () => {
    const ch = $('smChan').value, s = out.series[ch];
    if (!s) return;
    const idx = Object.keys(out.series).indexOf(ch);
    const mk = (id, label, vals, col, dash) => draw(id, {type: 'line',
      data: {labels: s.dates, datasets: [{label, data: vals, borderColor: col, backgroundColor: col,
        borderWidth: 2, borderDash: dash, pointRadius: 0, pointHitRadius: 8}]},
      options: {maintainAspectRatio: false, interaction: {mode: 'index', intersect: false},
        plugins: {legend: {display: false}, title: {display: true, text: label, color: INK, font: {weight: '700', size: 12}},
          tooltip: {callbacks: {label: c => ` ${fmt2(c.parsed.y)}`}}},
        scales: {x: {ticks: {maxTicksLimit: 6, maxRotation: 0}, grid: {display: false}},
                 y: {ticks: {callback: v => fmt(v)}, grid: {color: GRIDC}}}}});
    mk('edaSpendTs', nice(ch) + ' — weekly spend', s.spend, color(idx));
    if (s.units) mk('edaUnitsTs', nice(ch) + ' — weekly media units', s.units, color(idx), [6, 4]);
    if (s.cost_per_1k && s.cost_per_1k.length)
      mk('edaCpuTs', nice(ch) + ' — cost per 1k units', s.cost_per_1k, INK);
  };
  $('smChan').addEventListener('change', drawTs);
  drawTs();
}

/* ---------- EDA 2: variables (box plots) ---------- */
function boxChart(id, boxes, colorFn) {
  const labels = boxes.map(b => nice(b.variable));
  draw(id, {data: {labels, datasets: [
      {type: 'bar', label: 'whisker', data: boxes.map(b => [b.whisker_lo, b.whisker_hi]),
       backgroundColor: '#B9B9CF', barThickness: 2, borderWidth: 0},
      {type: 'bar', label: 'IQR', data: boxes.map(b => [b.q1, b.q3]),
       backgroundColor: boxes.map((b, i) => colorFn(b, i)), borderColor: '#fff', borderWidth: 2,
       borderRadius: 4, barThickness: 16},
      {type: 'scatter', label: 'median', data: boxes.map((b, i) => ({x: b.median, y: i})),
       backgroundColor: '#fff', borderColor: INK, pointRadius: 4.5, pointBorderWidth: 2},
    ]},
    options: {indexAxis: 'y', maintainAspectRatio: false,
      plugins: {legend: {display: false}, tooltip: {callbacks: {
        label: c => {
          const b = boxes[c.dataIndex];
          return c.dataset.label === 'median'
            ? ` median ${fmt2(b.median)}`
            : ` q1 ${fmt2(b.q1)} · q3 ${fmt2(b.q3)} · whiskers ${fmt2(b.whisker_lo)}…${fmt2(b.whisker_hi)} · ${b.n_outliers} outliers`;
        }}}},
      scales: {x: {grid: {color: GRIDC}, title: {display: true, text: 'scaled value'}},
               y: {grid: {display: false}, stacked: false}}}});
}

function renderVariables(out) {
  $('edaBody').innerHTML = `
    ${paramBox('variables', [
      {key: 'iqr_k', label: 'IQR multiplier k', step: 0.1},
      {key: 'std_threshold', label: 'KPI std error <', step: 0.0001},
      {key: 'scale_population', label: 'Population-scale first', type: 'check'},
    ])}
    <div class="grid cols2">
      <div class="card"><h2>Paid &amp; organic media (scaled)</h2>
        <div class="sub">Population-scaled, standardized — the shape Meridian actually models.</div>
        <div class="chartbox"><canvas id="boxMedia"></canvas></div></div>
      <div class="card"><h2>Controls &amp; non-media treatments (scaled)</h2>
        <div class="sub">Constant or near-constant variables carry no signal and must be dropped.</div>
        <div class="chartbox"><canvas id="boxCtrl"></canvas></div></div>
    </div>
    <div class="grid cols2" style="margin-top:18px;">
      <div class="card"><h2>KPI (scaled)</h2><div class="chartbox mini"><canvas id="boxKpi"></canvas></div></div>
      <div class="card"><h2>Variation &amp; outlier detail</h2>
        <div class="tblwrap"><table class="data"><thead><tr><th>Variable</th><th>Group</th>
          <th class="num">Outliers</th><th class="num">Std (scaled)</th><th class="num">Std w/o outliers</th><th>Status</th></tr></thead>
          <tbody>${out.details.map(d => `<tr><td>${esc(d.variable)}</td><td>${d.group}</td>
            <td class="num">${d.n_outliers}</td><td class="num">${d.std_scaled}</td>
            <td class="num">${d.std_wo_outliers}</td><td>${badge(d.status)}</td></tr>`).join('')}</tbody></table></div>
        <div class="note">Top outliers per variable are in the tooltip of each box; the five most extreme rows overall:
          ${out.details.flatMap(d => d.top_outliers.map(o => ({...o, v: d.variable}))).slice(0, 5)
             .map(o => `${esc(o.v)} @ ${esc(o.geo)} ${esc(o.time).slice(0, 10)} = ${fmt(o.value)}`).join(' · ') || 'none'}</div></div>
    </div>
    ${findingsList(out.findings)}
    ${aiCard(out.ai_summary)}`;

  const mediaBoxes = [...out.boxes.media, ...out.boxes.organic];
  boxChart('boxMedia', mediaBoxes, (b, i) => color(i));
  boxChart('boxCtrl', out.boxes.controls, () => color(0));
  boxChart('boxKpi', out.boxes.kpi, () => color(2));
}

/* ---------- EDA 3: population scaling ---------- */
function renderPopulation(out) {
  if (!out.applicable) {
    $('edaBody').innerHTML = `<div class="banner blue">${esc(out.ai_summary)}</div>`;
    return;
  }
  $('edaBody').innerHTML = `
    ${paramBox('population', [{key: 'high_corr', label: 'High-corr threshold |ρ| ≥', step: 0.05}])}
    <div class="grid cols2">
      <div class="card"><h2>KPI per 1,000 population, by brand</h2>
        <div class="sub">Population scaling puts brands of different size on a comparable footing.</div>
        <div class="chartbox short"><canvas id="popKpi"></canvas></div></div>
      <div class="card"><h2>Population vs media — Spearman ρ</h2>
        <div class="sub">Raw media should rise with population (positive ρ). Controls that track population may need population scaling in ModelSpec.</div>
        <div class="tblwrap"><table class="data"><thead><tr><th>Variable</th><th class="num">ρ</th><th>Reading</th></tr></thead><tbody>
          ${out.raw_media_vs_population.map(r => `<tr><td>${esc(r.variable)}</td>
             <td class="num">${r.spearman_rho ?? '—'}</td><td>${(r.spearman_rho ?? 0) > 0 ? 'as expected' : badge('INFO') + ' review'}</td></tr>`).join('')}
          ${out.controls_vs_population.map(r => `<tr><td>${esc(r.variable)} <span class="note">(control)</span></td>
             <td class="num">${r.spearman_rho ?? '—'}</td><td>${r.suggest_population_scaling ? badge('INFO') + ' suggest population scaling' : 'ok as-is'}</td></tr>`).join('')}
        </tbody></table></div></div>
    </div>
    <div class="card" style="margin-top:18px;"><h2>Brand footprint</h2>
      <div class="tblwrap"><table class="data"><thead><tr><th>Brand</th><th class="num">Population</th>
        <th class="num">KPI / 1k pop</th><th class="num">Media units / 1k pop</th><th class="num">Spend / 1k pop</th></tr></thead>
        <tbody>${out.per_capita.map(p => `<tr><td>${esc(p.geo)}</td><td class="num">${fmt(p.population)}</td>
          <td class="num">${fmt(p.kpi_per_1k)}</td><td class="num">${fmt(p.media_units_per_1k)}</td>
          <td class="num">${fmt(p.spend_per_1k)}</td></tr>`).join('')}</tbody></table></div></div>
    ${findingsList(out.findings)}
    ${aiCard(out.ai_summary)}`;

  const pc = out.per_capita.slice().sort((a, b) => b.kpi_per_1k - a.kpi_per_1k);
  draw('popKpi', {type: 'bar', data: {labels: pc.map(p => p.geo),
    datasets: [{data: pc.map(p => p.kpi_per_1k), backgroundColor: color(0), borderColor: '#fff',
                borderWidth: 2, borderRadius: 4, maxBarThickness: 34}]},
    options: {indexAxis: 'y', maintainAspectRatio: false, plugins: {legend: {display: false},
      tooltip: {callbacks: {label: c => ` ${fmt(c.parsed.x)} per 1k population`}}},
      scales: {x: {ticks: {callback: v => fmt(v)}, grid: {color: GRIDC}}, y: {grid: {display: false}}}}});
}

/* ---------- EDA 4: relationships ---------- */
function heatColor(r) {
  const a = Math.min(Math.abs(r), 1);
  if (a < 0.02) return 'background:#F6F6FC;color:#8585A3';
  const col = r > 0 ? '226,93,74' : '42,111,176';
  const txt = a > 0.6 ? '#fff' : INK;
  return `background:rgba(${col},${(0.12 + 0.88 * a).toFixed(2)});color:${txt}`;
}
function renderRelationships(out) {
  const n = out.variables.length;
  const head = '<tr><th></th>' + out.variables.map(v => `<th class="rot" title="${esc(v)}">${esc(nice(v)).slice(0, 12)}</th>`).join('') + '</tr>';
  const rows = out.variables.map((v, i) => '<tr><th style="text-align:right;padding-right:8px;">' + esc(nice(v)).slice(0, 16) + '</th>' +
    out.corr_matrix[i].map((r, j) => `<td style="${heatColor(i === j ? 0 : r)}" title="${esc(v)} × ${esc(out.variables[j])}: ${r}">${i === j ? '' : (Math.abs(r) >= 0.995 ? (r > 0 ? '1' : '-1') : r.toFixed(2).replace('0.', '.').replace('-0.', '-.'))}</td>`).join('') + '</tr>').join('');
  $('edaBody').innerHTML = `
    ${paramBox('relationships', [
      {key: 'corr_attention', label: 'Attention |r| ≥', step: 0.05},
      {key: 'corr_error', label: 'Error |r| ≥', step: 0.001},
      {key: 'vif_threshold', label: 'VIF error ≥', step: 10},
    ])}
    <div class="grid cols2">
      <div class="card"><h2>Pairwise correlation (national, scaled)</h2>
        <div class="sub">Blue = negative, red = positive. High |r| pairs share credit in the model.</div>
        <div class="tblwrap"><table class="heat">${head}${rows}</table></div></div>
      <div class="card"><h2>Variance inflation factors</h2>
        <div class="sub">VIF ≈ 1 is ideal; ≥ 10 notable; ≥ ${fmt(EDA.params.relationships.vif_threshold)} means near-perfect collinearity.</div>
        <div class="tblwrap"><table class="data"><thead><tr><th>Variable</th><th class="num">VIF</th><th>Status</th></tr></thead>
        <tbody>${out.vif.map(v => `<tr><td>${esc(v.variable)}</td><td class="num">${fmt2(v.vif)}</td><td>${badge(v.status)}</td></tr>`).join('')}</tbody></table></div>
        ${out.geo_r2.length ? `<div class="note" style="margin-top:12px;"><b>Most brand-bound variables</b> (R² vs geo): ${out.geo_r2.map(g => `${esc(g.variable)} ${g.r2_vs_geo}`).join(' · ')}</div>` : ''}
        <div class="note"><b>Most time-bound</b> (R² vs time): ${out.time_r2.map(g => `${esc(g.variable)} ${g.r2_vs_time}`).join(' · ')}</div></div>
    </div>
    ${findingsList(out.findings)}
    ${aiCard(out.ai_summary)}`;
}

/* ---------- EDA 5: priors ---------- */
function lognPdf(x, mu, sigma) {
  if (x <= 0) return 0;
  return Math.exp(-Math.pow(Math.log(x) - mu, 2) / (2 * sigma * sigma)) / (x * sigma * Math.sqrt(2 * Math.PI));
}
function renderPriors(out) {
  out.channels.forEach(c => { if (!PRIORS[c.channel]) PRIORS[c.channel] = {mu: c.mu, sigma: c.sigma}; });
  const nb = out.prior_negative_baseline_prob;
  $('edaBody').innerHTML = `
    ${paramBox('priors', [
      {key: 'neg_attention', label: 'P(neg) attention', step: '0.05'},
      {key: 'neg_error', label: 'P(neg) error', step: '0.05'},
    ])}
    <div class="tiles">
      <div class="tile"><div class="k">Default prior</div><div class="v" style="font-size:18px;">LogNormal(0.2, 0.9)</div></div>
      <div class="tile"><div class="k">Total ${esc(out.outcome_label)}</div><div class="v">${fmt(out.outcome_total)}</div></div>
      <div class="tile ${nb != null && nb < 0.2 ? 'accent' : ''}"><div class="k">P(negative baseline) under priors</div>
        <div class="v" style="color:${nb >= 0.5 ? 'var(--bad)' : nb >= 0.2 ? 'var(--warn)' : 'var(--good)'}">${nb == null ? '—' : (nb * 100).toFixed(1) + '%'}</div></div>
    </div>
    <div class="grid cols2">
      <div class="card"><h2>ROI prior per channel</h2>
        <div class="sub">μ = ln(expected ROI); σ = uncertainty (0.3–0.5 strong evidence · 0.7 directional · 0.9 vague default). Edit and save — the Meridian run uses these.</div>
        <div class="tblwrap"><table class="data"><thead><tr><th>Channel</th><th class="num" style="width:76px;">μ</th>
          <th class="num" style="width:76px;">σ</th><th class="num">Median ROI</th><th class="num">Mean</th>
          <th class="num">90% CI</th><th class="num">Prior contribution</th></tr></thead><tbody id="priorRows">
          ${out.channels.map((c, i) => `<tr data-ch="${esc(c.channel)}">
            <td><span class="sw" style="display:inline-block;width:9px;height:9px;border-radius:3px;background:${color(i)};margin-right:8px;"></span>${esc(c.channel)}</td>
            <td class="num"><input type="number" step="0.05" value="${PRIORS[c.channel].mu}" data-k="mu" style="width:74px;"></td>
            <td class="num"><input type="number" step="0.05" value="${PRIORS[c.channel].sigma}" data-k="sigma" style="width:74px;"></td>
            <td class="num" data-cell="med">${c.prior_roi_median}</td>
            <td class="num" data-cell="mean">${c.prior_roi_mean}</td>
            <td class="num" data-cell="ci">${c.prior_roi_90ci[0]}–${c.prior_roi_90ci[1]}</td>
            <td class="num">${c.prior_mean_contribution_pct}%</td></tr>`).join('')}
        </tbody></table></div>
        <div style="margin-top:14px; display:flex; gap:10px;">
          <button class="btn sm" id="btnSavePriors">Save priors &amp; re-check</button>
          <button class="btn ghost sm" id="btnResetPriors">Reset to defaults</button>
        </div></div>
      <div class="card"><h2>Prior densities</h2>
        <div class="sub">What each prior believes about ROI before seeing data.</div>
        <div class="chartbox tall"><canvas id="priorDens"></canvas></div></div>
    </div>
    ${findingsList(out.findings)}
    ${aiCard(out.ai_summary)}`;

  const drawDens = () => {
    const xs = Array.from({length: 160}, (_, i) => 0.02 + i * (8 / 160));
    const sets = out.channels.map((c, i) => {
      const p = PRIORS[c.channel];
      return {label: nice(c.channel), data: xs.map(x => ({x, y: lognPdf(x, p.mu, p.sigma)})),
              borderColor: color(i), backgroundColor: color(i), borderWidth: 2, pointRadius: 0, showLine: true};
    });
    draw('priorDens', {type: 'scatter', data: {datasets: sets},
      options: {maintainAspectRatio: false, parsing: false,
        plugins: {legend: {position: 'bottom'},
          tooltip: {callbacks: {label: c => ` ${c.dataset.label}: density ${c.parsed.y.toFixed(3)} at ROI ${c.parsed.x.toFixed(2)}`}}},
        scales: {x: {title: {display: true, text: 'ROI'}, grid: {color: GRIDC}},
                 y: {title: {display: true, text: 'prior density'}, grid: {color: GRIDC}, ticks: {display: false}}}}});
  };
  drawDens();

  $('priorRows').addEventListener('input', e => {
    const tr = e.target.closest('tr'); if (!tr) return;
    const ch = tr.dataset.ch;
    const mu = parseFloat(tr.querySelector('[data-k=mu]').value);
    const sigma = parseFloat(tr.querySelector('[data-k=sigma]').value);
    if (isFinite(mu) && isFinite(sigma) && sigma > 0) {
      PRIORS[ch] = {mu, sigma};
      tr.querySelector('[data-cell=med]').textContent = Math.exp(mu).toFixed(2);
      tr.querySelector('[data-cell=mean]').textContent = Math.exp(mu + sigma * sigma / 2).toFixed(2);
      tr.querySelector('[data-cell=ci]').textContent =
        `${Math.exp(mu - 1.645 * sigma).toFixed(2)}–${Math.exp(mu + 1.645 * sigma).toFixed(2)}`;
      drawDens();
    }
  });
  $('btnSavePriors').onclick = async () => {
    try {
      await post('/api/priors', {priors: PRIORS});
      EDA.cache.priors = null;
      toast('Priors saved — re-running the prior checks…');
      await runEda('priors');
    } catch (e) { toast(e.message, true); }
  };
  $('btnResetPriors').onclick = async () => {
    PRIORS = {}; await post('/api/priors', {priors: {}});
    EDA.cache.priors = null; await runEda('priors');
  };
}

/* ---------- EDA 6: complete analysis ---------- */
function renderComplete(out) {
  const cls = {ERROR: 'red', ATTENTION: 'amber', INFO: 'blue', OK: 'green'}[out.overall_status];
  $('edaBody').innerHTML = `
    ${paramBox('complete', [])}
    <div class="banner ${cls}"><b>${out.overall_status === 'OK' ? 'READY' : out.overall_status}</b> — ${esc(out.verdict)}</div>
    <div class="tiles">
      <div class="tile"><div class="k">Errors</div><div class="v" style="color:${out.counts.ERROR ? 'var(--bad)' : 'var(--good)'}">${out.counts.ERROR}</div></div>
      <div class="tile"><div class="k">Attention</div><div class="v" style="color:${out.counts.ATTENTION ? 'var(--warn)' : 'var(--good)'}">${out.counts.ATTENTION}</div></div>
      <div class="tile"><div class="k">Info notes</div><div class="v">${out.counts.INFO}</div></div>
      ${Object.entries(out.section_status).map(([k, v]) => `<div class="tile"><div class="k">${esc(k)}</div><div class="v" style="font-size:15px;">${badge(v)}</div></div>`).join('')}
    </div>
    <div class="card"><h2>Every finding, worst first</h2>
      <div class="findings">${out.findings.length ? out.findings.map(f =>
        `<div class="finding">${badge(f.severity)}<span><b>${esc(f.section)}:</b> ${esc(f.text)}</span></div>`).join('')
        : '<div class="finding">' + badge('OK') + '<span>Nothing flagged anywhere — a clean bill of health.</span></div>'}</div></div>
    ${aiCard(out.ai_summary)}
    <div style="margin-top:16px; display:flex; gap:10px;">
      <button class="btn" onclick="gotoTab('model')">Proceed to Model →</button>
      <button class="btn ghost" onclick="EDA.cache={}; runEda('complete')">Re-run all checks</button>
    </div>`;
}

/* ============================== model run ============================== */
$('btnRun').addEventListener('click', async () => {
  const btn = $('btnRun');
  try {
    btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Preparing…';
    const m = await applyMapping();
    if (!m.channels.length) throw new Error('Pick at least one media channel');
    if (ENGINE === 'classic') {
      btn.innerHTML = '<span class="spin"></span> Fitting (classic)…';
      const controls = [...m.controls, ...m.non_media];
      RES = await post('/api/model/run', {
        date_col: m.time_col, kpi: m.kpi,
        channels: m.channels.map(c => c.spend_col || c.units_col),
        controls, passes: 2,
      });
      RES.engine = 'classic';
      RES.channel_names = m.channels.map(c => c.name);
      finishRun();
    } else {
      if (Object.keys(PRIORS).length) await post('/api/priors', {priors: PRIORS});
      await post('/api/meridian/run', {sampling: {
        n_chains: +$('mChains').value, n_adapt: +$('mAdapt').value,
        n_burnin: +$('mBurn').value, n_keep: +$('mKeep').value,
        seed: +$('mSeed').value, max_lag: +$('mLag').value,
        knots: $('mKnots').value ? +$('mKnots').value : null,
      }});
      $('mProgress').style.display = '';
      $('mLog').textContent = 'Starting Meridian MCMC…';
      btn.innerHTML = '<span class="spin"></span> Sampling…';
      $('runNote').textContent = 'Full Bayesian sampling — minutes, depending on chains × draws and your CPU/GPU.';
      pollTimer = setInterval(pollMeridian, 2500);
      return; // button re-enabled by poller
    }
  } catch (e) { toast(e.message, true); }
  btn.disabled = false; btn.textContent = 'Run model';
});

async function pollMeridian() {
  try {
    const s = await api('/api/meridian/status');
    $('mLog').textContent = s.progress.join('\n') || 'working…';
    $('mLog').scrollTop = $('mLog').scrollHeight;
    if (s.state === 'done') {
      clearInterval(pollTimer);
      RES = await api('/api/meridian/results');
      RES.reports = s.reports;
      finishRun();
      $('btnRun').disabled = false; $('btnRun').textContent = 'Run model';
      $('runNote').textContent = '';
    } else if (s.state === 'error') {
      clearInterval(pollTimer);
      toast('Meridian failed: ' + s.error, true);
      $('btnRun').disabled = false; $('btnRun').textContent = 'Run model';
    }
  } catch (e) { /* transient poll errors are fine */ }
}

function finishRun() {
  $('tabResults').disabled = false; $('tabOpt').disabled = false;
  renderResults(); prepOptimizer();
  toast(`Model fitted — R² ${RES.r2 != null ? RES.r2.toFixed(2) : '—'}`);
  gotoTab('results');
}

/* ============================== results ============================== */
function renderResults() {
  const r = RES;
  const isM = r.engine === 'meridian';
  const chLabel = i => nice(isM ? r.channels[i] : (r.channel_names ? r.channel_names[i] : r.channels[i]));

  $('resBanner').innerHTML = isM
    ? `<div class="banner blue">Engine: <b>Google Meridian</b> (Bayesian). ${r.rhat_max != null ? `Convergence max R-hat = <b>${r.rhat_max.toFixed(3)}</b> ${r.rhat_max < 1.1 ? '✓ (target < 1.1)' : '⚠ above 1.1 — sample longer'}` : ''}
       ${r.reports && r.reports.model_summary ? ` · <a href="${r.reports.model_summary}" target="_blank" style="color:var(--mars-blue);font-weight:700;">Open Meridian's full HTML report ↗</a>` : ''}</div>`
    : `<div class="banner blue">Engine: <b>classic ridge</b> — instant approximation. Run Meridian for credible intervals and calibrated priors.</div>`;

  const mediaTotal = isM ? r.media_driven_total : r.summary.reduce((s, x) => s + x.total_contribution, 0);
  const kpiTotal = isM ? r.total_outcome : r.actual.reduce((a, b) => a + b, 0);
  $('tiles').innerHTML = `
    <div class="tile accent"><div class="k">Model fit R²</div><div class="v">${r.r2 != null ? r.r2.toFixed(2) : '—'}</div></div>
    <div class="tile"><div class="k">${isM ? 'MAPE' : 'Holdout error'}</div><div class="v">${r.holdout_mape != null ? (r.holdout_mape * 100).toFixed(1) : '—'}<small>%</small></div></div>
    ${isM ? `<div class="tile"><div class="k">Max R-hat</div><div class="v">${r.rhat_max != null ? r.rhat_max.toFixed(2) : '—'}</div></div>` :
            `<div class="tile"><div class="k">Periods modeled</div><div class="v">${r.dates.length}</div></div>`}
    <div class="tile"><div class="k">Media-driven share of ${esc(r.kpi)}</div><div class="v">${pct(mediaTotal / kpiTotal)}</div></div>`;
  $('fitSub').textContent = isM
    ? `${r.kpi} (${r.kpi_type}) · posterior mean vs actual, national aggregate`
    : `${r.kpi} · ridge α=${r.alpha} · last 20% of weeks held out during hyperparameter search`;

  const labels = r.dates;
  draw('chFit', {type: 'line', data: {labels, datasets: [
      {label: 'Actual', data: r.actual, borderColor: INK, backgroundColor: INK, borderWidth: 2, pointRadius: 0, pointHitRadius: 8},
      {label: isM ? 'Expected (posterior mean)' : 'Predicted', data: r.predicted, borderColor: color(0), backgroundColor: color(0), borderWidth: 2, borderDash: [6, 4], pointRadius: 0, pointHitRadius: 8},
    ]},
    options: {maintainAspectRatio: false, interaction: {mode: 'index', intersect: false},
      plugins: {legend: {position: 'bottom'}, tooltip: {callbacks: {label: c => ` ${c.dataset.label}: ${fmt(c.parsed.y)}`}}},
      scales: {x: {ticks: {maxTicksLimit: 10, maxRotation: 0}, grid: {display: false}},
               y: {ticks: {callback: v => fmt(v)}, grid: {color: GRIDC}}}}});

  /* decomposition */
  const hasWeekly = r.contributions && Object.keys(r.contributions).length;
  $('decompTitle').textContent = hasWeekly ? 'What drove the KPI each week' : 'Baseline vs media-driven';
  $('decompSub').textContent = hasWeekly
    ? 'Baseline (trend, seasonality & controls) plus the incremental contribution of every media channel.'
    : 'Posterior-mean baseline vs everything media explains on top of it (per-channel weekly split is in the ROI chart & table).';
  let dsets;
  if (hasWeekly) {
    dsets = [{label: 'Baseline', data: r.baseline, backgroundColor: BASELINE_GRAY, borderColor: '#fff',
              borderWidth: 1, fill: true, pointRadius: 0, pointHitRadius: 6, stack: 's', order: 99}];
    r.channels.forEach((ch, i) => dsets.push({label: chLabel(i), data: r.contributions[ch],
      backgroundColor: color(i), borderColor: '#fff', borderWidth: 1, fill: true, pointRadius: 0, pointHitRadius: 6, stack: 's'}));
  } else {
    const media = r.predicted.map((v, i) => Math.max(v - r.baseline[i], 0));
    dsets = [
      {label: 'Baseline', data: r.baseline, backgroundColor: BASELINE_GRAY, borderColor: '#fff', borderWidth: 1, fill: true, pointRadius: 0, stack: 's', order: 99},
      {label: 'Media-driven', data: media, backgroundColor: color(0), borderColor: '#fff', borderWidth: 1, fill: true, pointRadius: 0, stack: 's'},
    ];
  }
  draw('chDecomp', {type: 'line', data: {labels, datasets: dsets},
    options: {maintainAspectRatio: false, interaction: {mode: 'index', intersect: false},
      plugins: {legend: {position: 'bottom'}, tooltip: {callbacks: {label: c => ` ${c.dataset.label}: ${fmt(c.parsed.y)}`}}},
      scales: {x: {ticks: {maxTicksLimit: 10, maxRotation: 0}, grid: {display: false}},
               y: {stacked: true, ticks: {callback: v => fmt(v)}, grid: {color: GRIDC}}},
      elements: {line: {tension: .25}}}});

  /* ROI with CI */
  const s = [...r.summary].sort((a, b) => b.roi - a.roi);
  $('roiSub').textContent = isM ? 'Posterior mean · bars show the 90% credible interval.' : 'Total modeled contribution ÷ total spend, per channel.';
  const roiSets = [];
  if (isM && s[0].roi_lo != null) {
    roiSets.push({type: 'bar', label: '90% CI', data: s.map(x => [x.roi_lo, x.roi_hi]),
      backgroundColor: 'rgba(70,70,216,.25)', borderRadius: 4, barThickness: 14});
    roiSets.push({type: 'scatter', label: 'Posterior mean', data: s.map((x, i) => ({x: x.roi, y: i})),
      backgroundColor: color(0), borderColor: '#fff', pointRadius: 5.5, pointBorderWidth: 2});
  } else {
    roiSets.push({type: 'bar', label: 'ROI', data: s.map(x => x.roi), backgroundColor: color(0),
      borderColor: '#fff', borderWidth: 2, borderRadius: 4, maxBarThickness: 38});
  }
  draw('chRoi', {data: {labels: s.map(x => nice(x.channel)), datasets: roiSets},
    options: {indexAxis: 'y', maintainAspectRatio: false,
      plugins: {legend: {display: isM}, tooltip: {callbacks: {label: c =>
        c.dataset.type === 'scatter' ? ` mean ${fmt2(c.parsed.x)}` :
        Array.isArray(c.raw) ? ` 90% CI ${fmt2(c.raw[0])} – ${fmt2(c.raw[1])}` : ` ${fmt2(c.parsed.x)} per unit spend`}}},
      scales: {x: {ticks: {callback: v => fmt2(v)}, grid: {color: GRIDC}}, y: {grid: {display: false}}}}});

  /* share */
  const byShare = [...r.summary].sort((a, b) => b.contribution_share_of_media - a.contribution_share_of_media);
  draw('chShare', {type: 'bar', data: {labels: byShare.map(x => nice(x.channel)),
      datasets: [{label: 'Share', data: byShare.map(x => x.contribution_share_of_media * 100),
                  backgroundColor: byShare.map(x => color(r.summary.findIndex(y => y.channel === x.channel))),
                  borderColor: '#fff', borderWidth: 2, borderRadius: 4, maxBarThickness: 38}]},
    options: {indexAxis: 'y', maintainAspectRatio: false,
      plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => ` ${c.parsed.x.toFixed(1)}% of media-driven ${r.kpi}`}}},
      scales: {x: {ticks: {callback: v => v + '%'}, grid: {color: GRIDC}}, y: {grid: {display: false}}}}});

  /* curves */
  const haveCurves = r.curves && Object.keys(r.curves).length;
  $('curvesCard').style.display = haveCurves ? '' : 'none';
  if (haveCurves) {
    const chans = Object.keys(r.curves);
    const curveSets = chans.map((ch, i) => ({label: nice(ch),
      data: r.curves[ch].x.map((x, j) => ({x, y: r.curves[ch].y[j]})),
      borderColor: color(i), backgroundColor: color(i), borderWidth: 2, pointRadius: 0, pointHitRadius: 8, showLine: true}));
    chans.forEach((ch, i) => {
      const cw = r.curves[ch].current_weekly;
      const xs = r.curves[ch].x;
      const idx = xs.reduce((best, x, j) => Math.abs(x - cw) < Math.abs(xs[best] - cw) ? j : best, 0);
      curveSets.push({label: nice(ch) + ' (current)', data: [{x: cw, y: r.curves[ch].y[idx]}],
        backgroundColor: color(i), borderColor: '#fff', pointRadius: 5.5, pointBorderWidth: 2, showLine: false});
    });
    draw('chCurves', {type: 'scatter', data: {datasets: curveSets},
      options: {maintainAspectRatio: false, parsing: false,
        plugins: {legend: {position: 'bottom', labels: {filter: it => !it.text.includes('(current)')}},
          tooltip: {callbacks: {label: c => ` ${c.dataset.label}: spend ${fmt(c.parsed.x)} → ${fmt(c.parsed.y)}`}}},
        scales: {x: {title: {display: true, text: 'steady weekly spend'}, ticks: {callback: v => fmt(v)}, grid: {color: GRIDC}},
                 y: {title: {display: true, text: 'expected weekly contribution'}, ticks: {callback: v => fmt(v)}, grid: {color: GRIDC}}}}});
  }

  /* table */
  $('summaryTable').innerHTML = '<table class="data"><thead><tr>' +
    '<th>Channel</th><th class="num">Spend</th><th class="num">Contribution</th><th class="num">Share of media</th>' +
    `<th class="num">ROI${isM ? ' (mean)' : ''}</th>` + (isM ? '<th class="num">ROI 90% CI</th>' : '<th class="num">Adstock decay</th><th class="num">Half-saturation</th>') +
    '</tr></thead><tbody>' +
    r.summary.map((x, i) => `<tr>
      <td><span class="sw" style="display:inline-block;width:9px;height:9px;border-radius:3px;background:${color(i)};margin-right:8px;"></span>${nice(x.channel)}</td>
      <td class="num">${fmt(x.total_spend)}</td><td class="num">${fmt(x.total_contribution)}</td>
      <td class="num">${pct(x.contribution_share_of_media)}</td><td class="num"><b>${fmt2(x.roi)}</b></td>` +
      (isM ? `<td class="num">${fmt2(x.roi_lo)} – ${fmt2(x.roi_hi)}</td>`
           : `<td class="num">${x.decay != null ? x.decay.toFixed(2) : '—'}</td><td class="num">${fmt(x.half_sat_spend)}</td>`) +
      '</tr>').join('') + '</tbody></table>';
  $('summaryNote').textContent = isM
    ? 'Contribution = posterior-mean incremental outcome over the full period. CI = 90% credible interval.'
    : 'decay = weekly carry-over of the ad effect · half-saturation = spend level at 50% of the channel\'s max weekly effect.';
}

/* ============================== optimizer ============================== */
function prepOptimizer() {
  const totalWeekly = RES.summary.reduce((s, x) => s + (x.avg_weekly_spend || 0), 0);
  $('optBudget').value = Math.round(totalWeekly);
  $('optSub').textContent = RES.engine === 'meridian'
    ? 'Runs Meridian\'s BudgetOptimizer on the fitted posterior (fixed budget = weekly × 52). Also produces Meridian\'s HTML optimization report.'
    : 'Uses the fitted response curves to split a total weekly budget across channels for maximum expected KPI.';
}

$('btnOpt').addEventListener('click', async () => {
  const btn = $('btnOpt'); btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Optimizing…';
  try {
    if (RES.engine === 'meridian') {
      const d = await post('/api/meridian/optimize', {total_weekly_budget: +$('optBudget').value, n_weeks: 52});
      renderMeridianOpt(d);
    } else {
      const d = await post('/api/optimize', {total_weekly_budget: +$('optBudget').value,
        min_share: (+$('optMin').value) / 100, max_share: (+$('optMax').value) / 100});
      renderClassicOpt(d);
    }
  } catch (e) { toast(e.message, true); }
  btn.disabled = false; btn.textContent = 'Optimize allocation';
});

function renderClassicOpt(d) {
  $('optOut').style.display = 'block';
  const lift = d.expected_total_weekly_contribution - d.current_total_weekly_contribution;
  $('optTiles').innerHTML = `
    <div class="tile"><div class="k">Weekly budget</div><div class="v">${fmt(d.total_weekly_budget)}</div></div>
    <div class="tile"><div class="k">Expected weekly contribution</div><div class="v">${fmt(d.expected_total_weekly_contribution)}</div></div>
    <div class="tile"><div class="k">At current mix</div><div class="v">${fmt(d.current_total_weekly_contribution)}</div></div>
    <div class="tile accent"><div class="k">Expected lift</div><div class="v" style="color:${lift >= 0 ? 'var(--good)' : 'var(--bad)'}">${lift >= 0 ? '+' : ''}${fmt(lift)}</div></div>`;
  const rows = d.allocation;
  drawAlloc(rows.map(x => ({channel: x.channel, current: x.current_avg_weekly_spend, rec: x.recommended_weekly_spend})));
  $('allocTable').innerHTML = '<table class="data"><thead><tr>' +
    '<th>Channel</th><th class="num">Current /wk</th><th class="num">Recommended /wk</th><th class="num">Change</th></tr></thead><tbody>' +
    rows.map(x => {
      const chg = x.change_pct;
      const cls = chg == null ? '' : (chg >= 0 ? 'pos' : 'neg');
      const txt = chg == null ? 'new' : (chg >= 0 ? '+' : '') + chg.toFixed(0) + '%';
      return `<tr><td>${nice(x.channel)}</td><td class="num">${fmt(x.current_avg_weekly_spend)}</td>
              <td class="num"><b>${fmt(x.recommended_weekly_spend)}</b></td><td class="num ${cls}">${txt}</td></tr>`;
    }).join('') + '</tbody></table>';
  $('allocNote').textContent = '';
}

function renderMeridianOpt(d) {
  $('optOut').style.display = 'block';
  const pickMean = rows => rows.some(r => r.metric) ? rows.filter(r => r.metric === 'mean') : rows;
  const opt = pickMean(d.optimized || []), non = pickMean(d.nonoptimized || []);
  const get = (rows, ch, keys) => {
    const row = rows.find(r => (r.channel || r.channels) === ch) || {};
    for (const k of keys) if (row[k] != null) return row[k];
    return null;
  };
  const chans = [...new Set(opt.map(r => r.channel || r.channels))].filter(Boolean);
  const pairs = chans.map(ch => ({
    channel: ch,
    rec: get(opt, ch, ['spend', 'optimized_spend', 'budget']),
    current: get(non, ch, ['spend', 'nonoptimized_spend', 'budget']),
    inc_opt: get(opt, ch, ['incremental_outcome', 'incremental_revenue', 'mean']),
    inc_cur: get(non, ch, ['incremental_outcome', 'incremental_revenue', 'mean']),
  })).filter(p => p.rec != null);

  const totRec = pairs.reduce((s, p) => s + (p.rec || 0), 0);
  const totCur = pairs.reduce((s, p) => s + (p.current || 0), 0);
  const incRec = pairs.reduce((s, p) => s + (p.inc_opt || 0), 0);
  const incCur = pairs.reduce((s, p) => s + (p.inc_cur || 0), 0);
  $('optTiles').innerHTML = `
    <div class="tile"><div class="k">Optimized total spend</div><div class="v">${fmt(totRec)}</div></div>
    <div class="tile"><div class="k">Current total spend</div><div class="v">${fmt(totCur)}</div></div>
    <div class="tile"><div class="k">Expected incremental outcome</div><div class="v">${fmt(incRec)}</div></div>
    <div class="tile accent"><div class="k">vs current mix</div><div class="v" style="color:${incRec - incCur >= 0 ? 'var(--good)' : 'var(--bad)'}">${incRec - incCur >= 0 ? '+' : ''}${fmt(incRec - incCur)}</div></div>`;

  if (pairs.length) {
    drawAlloc(pairs.map(p => ({channel: p.channel, current: p.current || 0, rec: p.rec})));
    $('allocTable').innerHTML = '<table class="data"><thead><tr><th>Channel</th><th class="num">Current</th>' +
      '<th class="num">Optimized</th><th class="num">Change</th></tr></thead><tbody>' +
      pairs.map(p => {
        const chg = p.current ? (p.rec - p.current) / p.current * 100 : null;
        return `<tr><td>${nice(p.channel)}</td><td class="num">${fmt(p.current)}</td>
          <td class="num"><b>${fmt(p.rec)}</b></td>
          <td class="num ${chg == null ? '' : chg >= 0 ? 'pos' : 'neg'}">${chg == null ? '—' : (chg >= 0 ? '+' : '') + chg.toFixed(0) + '%'}</td></tr>`;
      }).join('') + '</tbody></table>';
  } else {
    $('allocTable').innerHTML = `<div class="note">${esc(d.note || 'Allocation table not exposed by this Meridian version — open the HTML report below.')}</div>`;
  }
  $('allocNote').innerHTML = d.report
    ? `<a class="btn ghost sm" href="${d.report}" target="_blank">Open Meridian's optimization report ↗</a>`
    : esc(d.note || '');
}

function drawAlloc(rows) {
  draw('chAlloc', {type: 'bar', data: {labels: rows.map(x => nice(x.channel)), datasets: [
      {label: 'Current', data: rows.map(x => x.current), backgroundColor: BASELINE_GRAY,
       borderColor: '#fff', borderWidth: 2, borderRadius: 4, maxBarThickness: 26},
      {label: 'Recommended', data: rows.map(x => x.rec), backgroundColor: color(0),
       borderColor: '#fff', borderWidth: 2, borderRadius: 4, maxBarThickness: 26},
    ]},
    options: {indexAxis: 'y', maintainAspectRatio: false,
      plugins: {legend: {position: 'bottom'}, tooltip: {callbacks: {label: c => ` ${c.dataset.label}: ${fmt(c.parsed.x)}`}}},
      scales: {x: {ticks: {callback: v => fmt(v)}, grid: {color: GRIDC}}, y: {grid: {display: false}}}}});
}

/* ============================== boot ============================== */
refreshFiles();
api('/api/meridian/availability').then(a => {
  MERIDIAN = a;
  $('engineBadge').textContent = a.available
    ? `Meridian ${a.version} ready · data stays on this machine`
    : 'Meridian not installed — classic engine only';
}).catch(() => {});
