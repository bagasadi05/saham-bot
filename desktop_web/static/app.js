const state = {
  tab: 'pasar',
  chatId: 'desktop',
  defaultProfileId: 'desktop',
  modal: 10000000,
  defaultModal: 10000000,
  syariah: false,
  refresh: '15m',
  refreshOptions: { Manual: 0, '5m': 5, '15m': 15, '30m': 30 },
  sinyalFilter: 'ALL',
  sinyalRows: [],
  sinyalSort: { col: 'rel_pct', asc: false },
  lastPaketTicket: '',
  lastCek: null,
  chart: null,
  chartOpts: { ma20: true, ma50: true, ma200: true, vol: true, levels: true },
  selectedAlertKinds: new Set(['breakout', 'vol_spike', 'oversold']),
  alertKinds: ['jump', 'drop', 'breakout', 'breakdown', 'oversold', 'overbought', 'ma_golden', 'ma_death', 'vol_spike'],
  universe: [],
};

const fmtNum = (v, d=0) => v == null ? '—' : Number(v).toLocaleString('id-ID', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtRp = (v) => v == null ? '—' : 'Rp' + fmtNum(Math.round(v), 0);
const fmtPct = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;
// --- Keamanan XSS: gunakan helper window.esc dari index.html (fallback lokal) ---
const esc = window.esc || (v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])));
// san: seperti esc, tapi tetap mengizinkan markup inti <b>/<i>/<br> dari output core
const san = v => esc(v).replace(/&lt;(\/?)(b|i|br)\s*\/?&gt;/gi, '<$1$2>');

function showToast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(window._toastT);
  window._toastT = setTimeout(() => el.classList.add('hidden'), 3200);
}

function setStatus(msg) {
  document.getElementById('sidebar-status').textContent = msg;
}

function setRefreshing(on) {
  const ic = document.getElementById('icon-refresh');
  if (ic) ic.classList.toggle('animate-spin', on);
  const bar = document.getElementById('top-loading-bar');
  if (bar) bar.classList.toggle('hidden', !on);
}

function getRecentTickers() {
  try {
    const saved = JSON.parse(localStorage.getItem('sb_recent_tickers') || '[]');
    if (Array.isArray(saved) && saved.length) return saved.slice(0, 6);
  } catch (_) {}
  return ['BRIS', 'PBID', 'BBCA', 'BBRI', 'BMRI', 'TLKM'];
}

function pushRecentTicker(sym) {
  const t = String(sym || '').trim().toUpperCase();
  if (!t) return;
  const list = [t, ...getRecentTickers().filter(x => x !== t)].slice(0, 6);
  try { localStorage.setItem('sb_recent_tickers', JSON.stringify(list)); } catch (_) {}
  renderRecentTickers();
}

function renderRecentTickers() {
  const list = getRecentTickers();
  const wrap = document.getElementById('recent-tickers-wrap');
  if (wrap) {
    wrap.innerHTML = list.map(t => `<button onclick="openCek('${esc(t)}')" class="px-2 py-1 rounded-md bg-slate-900/90 hover:bg-sky-500/20 border border-slate-800 hover:border-sky-500/40 mono text-[11px] text-slate-300 hover:text-sky-300 transition cursor-pointer">${esc(t)}</button>`).join('');
  }
  const cmdWrap = document.getElementById('cmd-tickers');
  if (cmdWrap) {
    cmdWrap.innerHTML = list.map(t => `<button onclick="closeCmdPalette();openCek('${esc(t)}')" class="px-2.5 py-1 rounded-md bg-slate-900 hover:bg-sky-600/30 border border-slate-700 mono text-xs text-sky-300 cursor-pointer transition">${esc(t)}</button>`).join('');
  }
}

async function loadUniverse() {
  try {
    const d = await apiFetch('/api/universe?limit=1000');
    state.universe = d.items || [];
    const dl = document.getElementById('idx-tickers-datalist');
    if (dl && state.universe.length) {
      dl.innerHTML = state.universe.map(s => `<option value="${esc(s.code)}">${esc(s.code)} — ${esc(s.name)}${s.syariah ? ' (ISSI)' : ''}</option>`).join('');
    }
  } catch (_) {}
}

function _matchUniverse(q, maxItems = 40) {
  const list = state.universe || [];
  const ql = String(q || '').trim().toLowerCase();
  const base = state.syariah ? list.filter(s => s.syariah) : list;
  if (!ql) {
    const rec = getRecentTickers();
    const popular = ['BBCA', 'BBRI', 'BMRI', 'BBNI', 'BRIS', 'TLKM', 'ASII', 'ICBP', 'ADRO', 'ANTM', 'PBID', 'UNTR'];
    const prio = [...new Set([...rec, ...popular])];
    const prioItems = prio.map(c => base.find(x => x.code === c)).filter(Boolean);
    const rest = base.filter(x => !prio.includes(x.code)).slice(0, Math.max(0, maxItems - prioItems.length));
    return [...prioItems, ...rest];
  }
  const exact = [];
  const starts = [];
  const contains = [];
  for (const s of base) {
    const cl = s.code.toLowerCase();
    const nl = (s.name || '').toLowerCase();
    const sl = (s.sector || '').toLowerCase();
    if (cl === ql) exact.push(s);
    else if (cl.startsWith(ql)) starts.push(s);
    else if (cl.includes(ql) || nl.includes(ql) || sl.includes(ql)) contains.push(s);
  }
  return [...exact, ...starts, ...contains].slice(0, maxItems);
}

function renderCekDropdown(query) {
  const dd = document.getElementById('cek-dropdown');
  if (!dd) return;
  const matches = _matchUniverse(query, 50);
  if (!matches.length) {
    dd.innerHTML = `<div class="px-3 py-4 text-center text-xs text-slate-500">Tidak ada emiten cocok dengan "${esc(query)}".</div>`;
    return;
  }
  dd.innerHTML = `<div class="px-3 py-1.5 text-[10px] uppercase tracking-wider text-slate-500 bg-slate-900/90 sticky top-0 flex justify-between"><span>${query ? `Hasil "${esc(query)}"` : 'Populer & Semua Emiten BEI'}</span><span>${matches.length} tampil / ${state.universe.length} BEI</span></div>` +
    matches.map(s => `<button type="button" onmousedown="selectCekTicker('${esc(s.code)}')" class="w-full px-3 py-2 text-left hover:bg-sky-500/15 flex items-center justify-between gap-2 cursor-pointer transition">
      <div class="min-w-0">
        <div class="flex items-center gap-1.5">
          <span class="mono text-xs font-bold text-sky-300">${esc(s.code)}</span>
          ${s.syariah ? '<span class="px-1 py-0.2 rounded bg-emerald-500/20 text-emerald-300 text-[9px] font-bold">ISSI</span>' : ''}
        </div>
        <div class="text-[11px] text-slate-300 truncate">${s.name || s.code}</div>
      </div>
      <span class="text-[10px] text-slate-500 shrink-0 max-w-[95px] truncate">${s.sector || s.board || ''}</span>
    </button>`).join('');
}

function openCekDropdown() {
  const dd = document.getElementById('cek-dropdown');
  const inp = document.getElementById('cek-input');
  if (!dd) return;
  // Jika input berisi tepat 4 huruf ticker yang sedang aktif, tampilkan daftar penuh agar mudah pilih
  const v = (inp?.value || '').trim();
  const isCurrentExact = v && state.lastCek?.ticker && v.toUpperCase() === state.lastCek.ticker.toUpperCase();
  renderCekDropdown(isCurrentExact ? '' : v);
  dd.classList.remove('hidden');
}

function closeCekDropdown() {
  const dd = document.getElementById('cek-dropdown');
  if (dd) dd.classList.add('hidden');
}

function toggleCekDropdown() {
  const dd = document.getElementById('cek-dropdown');
  if (!dd) return;
  if (dd.classList.contains('hidden')) {
    renderCekDropdown('');
    dd.classList.remove('hidden');
    document.getElementById('cek-input')?.focus();
  } else {
    dd.classList.add('hidden');
  }
}

function filterCekDropdown(val) {
  const dd = document.getElementById('cek-dropdown');
  if (!dd) return;
  renderCekDropdown(val);
  dd.classList.remove('hidden');
}

function selectCekTicker(code) {
  const inp = document.getElementById('cek-input');
  if (inp) inp.value = code;
  closeCekDropdown();
  loadCek();
}

document.addEventListener('click', (e) => {
  const wrap = document.getElementById('cek-picker-wrap');
  if (wrap && !wrap.contains(e.target)) closeCekDropdown();
});

function openCmdPalette() {
  renderRecentTickers();
  const m = document.getElementById('cmd-modal');
  const inp = document.getElementById('cmd-input');
  if (m) m.classList.remove('hidden');
  if (inp) { inp.value = ''; setTimeout(() => inp.focus(), 20); }
}

function closeCmdPalette() {
  const m = document.getElementById('cmd-modal');
  if (m) m.classList.add('hidden');
}

// MOBILE DRAWER, QR CODE MODAL & PIN LOCK
function toggleMobileDrawer() {
  const sb = document.getElementById('app-sidebar');
  const bd = document.getElementById('mobile-drawer-backdrop');
  if (!sb) return;
  const isClosed = sb.classList.contains('-translate-x-full');
  sb.classList.toggle('-translate-x-full', !isClosed);
  if (bd) bd.classList.toggle('hidden', !isClosed);
}

function closeMobileDrawer() {
  const sb = document.getElementById('app-sidebar');
  const bd = document.getElementById('mobile-drawer-backdrop');
  if (sb) sb.classList.add('-translate-x-full');
  if (bd) bd.classList.add('hidden');
}

async function openMobileModal() {
  document.getElementById('mobile-modal').classList.remove('hidden');
  const wrap = document.getElementById('mobile-urls-wrap');
  const qrEl = document.getElementById('mobile-qr');
  const pinSt = document.getElementById('mobile-pin-status');
  const pinSec = document.getElementById('mobile-pin-section');
  try {
    const d = await apiFetch('/api/mobile-info');
    if (pinSt) {
      pinSt.textContent = d.pin_enabled ? 'Aktif (Terkunci)' : 'Nonaktif';
      pinSt.className = `text-[10px] px-2 py-0.5 rounded-full ${d.pin_enabled ? 'bg-emerald-500/20 text-emerald-300' : 'bg-slate-800 text-slate-400'}`;
    }
    if (pinSec) pinSec.classList.toggle('hidden', !d.is_local);
    const urls = d.urls || [];
    if (!d.lan_enabled) {
      wrap.innerHTML = `<div class="p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-200">Mode LAN belum aktif. Jalankan lewat <b>run_desktop_modern.bat</b> agar bisa diakses dari HP.</div>`;
    } else if (!urls.length) {
      wrap.innerHTML = `<div class="text-slate-400">Tidak ada IP Wi-Fi/LAN terdeteksi. Pastikan PC terhubung ke Wi-Fi.</div>`;
    } else {
      wrap.innerHTML = urls.map(u => `<div class="p-2.5 rounded-lg bg-slate-900/90 border border-slate-800 flex items-center justify-between gap-2">
        <div class="min-w-0">
          <div class="text-[10px] text-slate-400">${u.label}</div>
          <div class="mono text-xs font-bold text-sky-300 truncate">${u.url}</div>
        </div>
        <button onclick="navigator.clipboard.writeText('${u.url}');showToast('URL disalin')" class="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-[11px] text-slate-200 cursor-pointer shrink-0">Salin</button>
      </div>`).join('');
    }
    if (qrEl) {
      qrEl.innerHTML = '';
      const primaryUrl = (urls[0] || {}).url || `http://127.0.0.1:${d.port}/`;
      if (window.QRCode) {
        new QRCode(qrEl, { text: primaryUrl, width: 168, height: 168, colorDark: '#050811', colorLight: '#ffffff' });
      } else {
        qrEl.innerHTML = `<div class="text-xs text-slate-800 mono p-2">${esc(primaryUrl)}</div>`;
      }
    }
  } catch (e) {
    wrap.innerHTML = `<div class="text-rose-400">Gagal memuat info HP: ${esc(e.message)}</div>`;
  }
}

function closeMobileModal() {
  document.getElementById('mobile-modal')?.classList.add('hidden');
}

async function saveMobilePin() {
  const pin = (document.getElementById('mobile-pin-input')?.value || '').trim();
  try {
    const res = await apiFetch('/api/mobile-pin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin }),
    });
    document.getElementById('mobile-pin-input').value = '';
    showToast(res.pin_enabled ? 'PIN akses HP diaktifkan' : 'PIN akses HP dimatikan');
    openMobileModal();
  } catch (e) {
    showToast(e.message);
  }
}

async function unlockMobilePin() {
  const pin = (document.getElementById('pin-unlock-input')?.value || '').trim();
  const errEl = document.getElementById('pin-unlock-err');
  try {
    await apiFetch('/api/auth/unlock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin }),
    });
    document.getElementById('pin-lock-overlay').classList.add('hidden');
    if (errEl) errEl.classList.add('hidden');
    await bootAppData();
  } catch (e) {
    if (errEl) { errEl.textContent = e.message || 'PIN salah'; errEl.classList.remove('hidden'); }
  }
}

function switchTab(name) {
  state.tab = name;
  closeMobileDrawer();
  document.querySelectorAll('.tab-view').forEach(el => el.classList.add('hidden'));
  document.getElementById(`view-${name}`)?.classList.remove('hidden');
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.classList.toggle('nav-active', btn.dataset.tab === name);
  });
  document.querySelectorAll('.mnav-btn').forEach(btn => {
    const active = btn.dataset.tab === name;
    btn.classList.toggle('text-sky-400', active);
    btn.classList.toggle('text-slate-400', !active);
  });
  if (name === 'pasar') loadMarket();
  else if (name === 'watchlist') loadWatchlist();
  else if (name === 'sinyal') loadSinyal();
  else if (name === 'paket') loadPaket();
  else if (name === 'portofolio') loadPortfolio();
  else if (name === 'jurnal') { loadJurnal(); loadAccuracy(); }
  else if (name === 'cek' && !state.lastCek) loadCek();
  else if (name === 'compare' && !window._compareLoaded) loadCompare();
  else if (name === 'backtest') {
    const inp = document.getElementById('bt-ticker');
    if (state.lastCek?.ticker && inp && !window._btLoaded) inp.value = state.lastCek.ticker;
    const mod = document.getElementById('bt-modal');
    if (mod && state.modal && !window._btLoaded) mod.value = Math.round(state.modal);
    if (!window._btLoaded) runFullBacktest();
  }
  else if (name === 'alerts') loadAlerts();
}

function refreshCurrent() {
  switchTab(state.tab);
}

async function apiFetch(url, opts={}) {
  const res = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Permintaan gagal');
  return data;
}

function syncRefreshSelectors() {
  const opts = state.refreshOptions || { Manual: 0, '5m': 5, '15m': 15, '30m': 30 };
  const keys = Object.keys(opts);
  const topSel = document.getElementById('auto-refresh-sel');
  if (topSel) {
    topSel.innerHTML = keys.map(k => `<option value="${esc(k)}">Auto: ${esc(k)}</option>`).join('');
    topSel.value = keys.includes(state.refresh) ? state.refresh : (keys[0] || 'Manual');
  }
  const modSel = document.getElementById('modal-refresh');
  if (modSel) {
    modSel.innerHTML = keys.map(k => {
      const m = Number(opts[k]) || 0;
      const lbl = m > 0 ? `Setiap ${m} menit` : 'Manual (Off)';
      return `<option value="${esc(k)}">${esc(lbl)}</option>`;
    }).join('');
    modSel.value = keys.includes(state.refresh) ? state.refresh : (keys[0] || 'Manual');
  }
}

async function loadProfile() {
  const p = await apiFetch(`/api/profile?chat_id=${encodeURIComponent(state.chatId)}`);
  state.chatId = p.chat_id;
  state.modal = p.modal;
  state.syariah = p.syariah;
  if (p.default_profile_id) state.defaultProfileId = p.default_profile_id;
  if (p.default_modal) state.defaultModal = Number(p.default_modal) || state.defaultModal;
  if (p.refresh_options && typeof p.refresh_options === 'object') state.refreshOptions = p.refresh_options;
  if (p.refresh) state.refresh = p.refresh;
  syncRefreshSelectors();
  applyAutoRefreshTimer(state.refresh, true);
  document.getElementById('top-modal-txt').textContent = p.modal >= 1e6 ? `Rp${(p.modal/1e6).toFixed(1).replace('.0','')} Jt` : fmtRp(p.modal);
  document.getElementById('syariah-dot').className = `w-2 h-2 rounded-full ${p.syariah ? 'bg-emerald-400' : 'bg-slate-600'}`;
  const syCb = document.getElementById('sinyal-syariah');
  if (syCb) syCb.checked = !!p.syariah;
  updateClock(p.clock, p.market_status);
}

function updateClock(txt, status) {
  if (!txt) return;
  document.getElementById('clock-txt').textContent = txt;
  const dot = document.getElementById('clock-dot');
  dot.className = `w-2 h-2 rounded-full ${status === 'open' ? 'bg-emerald-400 pulse-dot' : 'bg-slate-500'}`;
}

async function toggleSyariah() {
  state.syariah = !state.syariah;
  await apiFetch('/api/profile', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ chat_id: state.chatId, modal: state.modal, syariah: state.syariah, refresh: state.refresh }),
  });
  document.getElementById('syariah-dot').className = `w-2 h-2 rounded-full ${state.syariah ? 'bg-emerald-400' : 'bg-slate-600'}`;
  const syCb = document.getElementById('sinyal-syariah');
  if (syCb) syCb.checked = !!state.syariah;
  showToast(state.syariah ? 'Mode Syariah (ISSI) aktif' : 'Mode Syariah dimatikan');
  refreshCurrent();
}

async function toggleSinyalSyariah(checked) {
  if (!!checked === !!state.syariah) {
    loadSinyal();
    return;
  }
  await toggleSyariah();
}

function openProfileModal() {
  document.getElementById('modal-chat-id').value = state.chatId;
  document.getElementById('modal-amount').value = state.modal;
  const modSel = document.getElementById('modal-refresh');
  if (modSel) modSel.value = state.refresh || '15m';
  document.getElementById('profile-modal').classList.remove('hidden');
}
function closeProfileModal() { document.getElementById('profile-modal').classList.add('hidden'); }
async function saveProfile() {
  state.chatId = document.getElementById('modal-chat-id').value.trim() || state.defaultProfileId || 'desktop';
  state.modal = Number(document.getElementById('modal-amount').value) || state.defaultModal || 10000000;
  const modSel = document.getElementById('modal-refresh');
  if (modSel && modSel.value) state.refresh = modSel.value;
  await apiFetch('/api/profile', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ chat_id: state.chatId, modal: state.modal, syariah: state.syariah, refresh: state.refresh }),
  });
  closeProfileModal();
  await loadProfile();
  showToast('Profil & modal disimpan');
  refreshCurrent();
}

async function loadMarket() {
  setRefreshing(true);
  setStatus('Memuat kondisi pasar BEI…');
  try {
    const d = await apiFetch('/api/market');
    updateClock(d.clock, d.market_status);
    const ihsg = d.ihsg || {};
    const reg = ihsg.regime || {};
    const br = d.breadth || {};
    const news = d.news || {};

    const ret1d = ihsg.ret1d;
    const ihsgCls = ret1d > 0 ? 'text-emerald-400' : (ret1d < 0 ? 'text-rose-400' : 'text-white');
    document.getElementById('kpi-ihsg-val').className = `mono text-2xl font-bold mt-1.5 ${ihsgCls}`;
    document.getElementById('kpi-ihsg-val').textContent = ihsg.close ? fmtNum(ihsg.close, 1) : 'Tidak tersedia';
    document.getElementById('kpi-ihsg-sub').textContent = `1H ${fmtPct(ret1d)} · 20H ${fmtPct(ihsg.ret20d)}`;
    document.getElementById('kpi-ihsg-bar').className = `absolute top-0 inset-x-0 h-1 ${ret1d >= 0 ? 'bg-emerald-500' : 'bg-rose-500'}`;

    const scale = reg.exposure_scale ?? 1.0;
    const regMap = { DEFENSIVE: 'DEFENSIF', BULL: 'BULLISH', CAUTION: 'WASPADA' };
    const regShort = regMap[(reg.status || '').toUpperCase()] || (reg.status || 'NA');
    const regCls = scale >= 0.99 ? 'text-emerald-400' : (scale >= 0.45 ? 'text-amber-400' : 'text-rose-400');
    const regBg = scale >= 0.99 ? 'bg-emerald-500' : (scale >= 0.45 ? 'bg-amber-500' : 'bg-rose-500');
    document.getElementById('kpi-reg-val').className = `mono text-2xl font-bold mt-1.5 ${regCls}`;
    document.getElementById('kpi-reg-val').textContent = `${regShort} · ${Number(scale).toFixed(1)}x`;
    document.getElementById('kpi-reg-sub').textContent = reg.reason || 'SMA50 vs SMA200 ^JKSE';
    document.getElementById('kpi-reg-bar').className = `absolute top-0 inset-x-0 h-1 ${regBg}`;

    document.getElementById('top-ihsg').textContent = ihsg.close ? `IHSG ${fmtNum(ihsg.close, 0)} (${fmtPct(ret1d)})` : 'IHSG —';
    const topReg = document.getElementById('top-regime');
    topReg.textContent = `${regShort} ${Number(scale).toFixed(1)}x`;
    topReg.className = `px-2 py-0.5 rounded-full text-[11px] font-semibold ${scale >= 0.99 ? 'bg-emerald-500/20 text-emerald-300' : (scale >= 0.45 ? 'bg-amber-500/20 text-amber-300' : 'bg-rose-500/20 text-rose-300')}`;

    // Action Banner
    const banner = document.getElementById('action-banner');
    banner.classList.remove('hidden');
    if (scale <= 0.3) {
      banner.className = 'p-4 rounded-xl border border-rose-500/30 bg-rose-500/10 text-rose-300 flex items-start gap-3';
      document.getElementById('action-banner-body').textContent = `Pasar sangat defensif (skala ${scale.toFixed(1)}x). Utamakan proteksi modal, perkecil lot, dan hindari mengejar breakout.`;
    } else if (scale < 0.99) {
      banner.className = 'p-4 rounded-xl border border-amber-500/30 bg-amber-500/10 text-amber-300 flex items-start gap-3';
      document.getElementById('action-banner-body').textContent = `Pasar berhati-hati (skala ${scale.toFixed(1)}x). Pilih hanya kandidat terkuat dengan Stop-Loss ketat.`;
    } else {
      banner.className = 'p-4 rounded-xl border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 flex items-start gap-3';
      document.getElementById('action-banner-body').textContent = `Rezim pasar konstruktif (skala ${scale.toFixed(1)}x). Eksekusi rencana beli sesuai batas risiko.`;
    }

    // Breadth
    if (br.ok) {
      const adv = br.advancing || 0, dec = br.declining || 0, tot = Math.max(1, br.total || (adv + dec));
      const flat = Math.max(0, tot - adv - dec);
      document.getElementById('kpi-br-val').textContent = `▲${adv} / ▼${dec}`;
      document.getElementById('kpi-br-val').className = `mono text-2xl font-bold mt-1.5 ${adv >= dec ? 'text-emerald-400' : 'text-rose-400'}`;
      document.getElementById('kpi-br-sub').textContent = `BELI ${br.beli || 0}/${tot} emiten`;
      document.getElementById('br-up-lbl').textContent = `▲ Naik ${adv} (${(adv/tot*100).toFixed(0)}%)`;
      document.getElementById('br-dn-lbl').textContent = `▼ Turun ${dec} (${(dec/tot*100).toFixed(0)}%)`;
      document.getElementById('br-up-bar').style.width = `${adv/tot*100}%`;
      document.getElementById('br-flat-bar').style.width = `${flat/tot*100}%`;
      document.getElementById('br-dn-bar').style.width = `${dec/tot*100}%`;
    } else {
      document.getElementById('kpi-br-val').textContent = 'Belum /scan';
      document.getElementById('kpi-br-sub').textContent = 'Jalankan Scan BEI di tab Sinyal';
    }

    // Condition & freshness
    const cond = d.condition || '—';
    const condCls = cond.includes('KONDUSIF') ? 'text-emerald-400' : (cond.includes('WASPADA') ? 'text-rose-400' : 'text-amber-400');
    document.getElementById('kpi-cond-val').className = `mono text-2xl font-bold mt-1.5 ${condCls}`;
    document.getElementById('kpi-cond-val').textContent = cond;
    document.getElementById('kpi-cond-sub').textContent = `${d.as_of_label || ''}${d.stale_age_text ? ' · ' + d.stale_age_text : ''}`;

    // Sector bars (live or fallback)
    const secWrap = document.getElementById('sector-bars');
    const secData = (d.sectors && d.sectors.ok) ? d.sectors : ((d.sectors_fallback && d.sectors_fallback.ok) ? d.sectors_fallback : null);
    document.getElementById('sector-source-lbl').textContent = secData
      ? (secData.source === 'snapshot /scan' ? 'Rata-rata 1H dari snapshot /scan lokal · klik untuk filter Sinyal' : 'Indeks Sektoral IDX-IC 1 Hari · klik untuk filter Sinyal')
      : 'Belum ada data sektor — jalankan Scan BEI';
    if (secData && secData.sectors?.length) {
      const maxAbs = Math.max(...secData.sectors.map(s => Math.abs(s.pct || 0)), 0.5);
      secWrap.innerHTML = secData.sectors.map(s => {
        const pct = Number(s.pct || 0);
        const w = Math.max(4, Math.abs(pct) / maxAbs * 100);
        const pos = pct >= 0;
        return `<div onclick="jumpSector('${s.name.replace(/'/g, "\\'")}')" class="group flex items-center gap-3 px-2.5 py-1.5 rounded-lg hover:bg-slate-800/70 transition cursor-pointer">
          <div class="w-40 text-xs text-slate-200 truncate group-hover:text-sky-300">${esc(s.name)}${s.n ? ` <span class="text-slate-500">(${s.n})</span>` : ''}</div>
          <div class="flex-1 h-2 bg-slate-800/90 rounded-full overflow-hidden">
            <div class="h-full rounded-full ${pos ? 'bg-emerald-500' : 'bg-rose-500'}" style="width:${w}%"></div>
          </div>
          <div class="w-16 text-right mono text-xs font-semibold ${pos ? 'text-emerald-400' : 'text-rose-400'}">${fmtPct(pct)}</div>
        </div>`;
      }).join('');
    } else {
      secWrap.innerHTML = `<div class="text-xs text-slate-500 py-8 text-center">Jalankan <b>Scan BEI</b> di tab Sinyal Pasar untuk mengisi rotasi sektor lokal.</div>`;
    }

    // Radar Top 5 Kandidat Paling Siap
    const tp = d.top_picks || {};
    const tpCard = document.getElementById('top-picks-card');
    if (tpCard && tp.rows?.length) {
      tpCard.classList.remove('hidden');
      document.getElementById('top-picks-sub').textContent = tp.quality_filtered
        ? 'Sinyal BELI + Fundamental Sehat (tertinggi dari snapshot /scan) · klik untuk bedah'
        : 'Sinyal BELI terkuat dari snapshot /scan terbaru · klik untuk bedah';
      document.getElementById('top-picks-grid').innerHTML = tp.rows.map(r => `<div onclick="openCek('${esc(r.ticker)}')" class="rounded-lg bg-slate-900/80 border border-slate-800 hover:border-emerald-500/50 p-3 cursor-pointer transition space-y-1">
        <div class="flex items-center justify-between"><span class="mono font-bold text-white text-sm">${esc(r.ticker)}</span><span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-300">Rank ${r.rel_pct != null ? Math.round(r.rel_pct) : '—'}%</span></div>
        <div class="text-[11px] text-slate-400 truncate">${esc(r.sector || '—')}</div>
        <div class="flex items-baseline justify-between pt-1"><span class="mono text-xs font-semibold text-slate-100">${fmtRp(r.price)}</span><span class="mono text-[11px] ${(r.ret1d||0)>=0?'text-emerald-400':'text-rose-400'}">${fmtPct(r.ret1d)}</span></div>
      </div>`).join('');
    } else if (tpCard) {
      tpCard.classList.add('hidden');
    }

    // Condition notes & News
    document.getElementById('condition-notes').innerHTML = (d.condition_notes || []).map(n => `<div class="flex items-center gap-2"><span class="w-1.5 h-1.5 rounded-full bg-sky-400"></span><span>${san(n)}</span></div>`).join('');
    const nb = document.getElementById('news-badge');
    if (news && news.n) {
      nb.textContent = `${news.label} (${news.pct >= 0 ? '+' : ''}${Math.round(news.pct || 0)}%)`;
      nb.className = `text-xs font-semibold px-2.5 py-1 rounded-full ${news.label === 'POSITIF' ? 'bg-emerald-500/20 text-emerald-300' : (news.label === 'NEGATIF' ? 'bg-rose-500/20 text-rose-300' : 'bg-amber-500/20 text-amber-300')}`;
      document.getElementById('news-list').innerHTML = (news.details || []).slice(0, 8).map(it => {
        const sc = it.sent || 0;
        const tag = sc >= 0.25 ? ['POSITIF', 'bg-emerald-500/15 text-emerald-400'] : (sc <= -0.25 ? ['NEGATIF', 'bg-rose-500/15 text-rose-400'] : ['NETRAL', 'bg-slate-800 text-slate-400']);
        return `<div class="p-2.5 rounded-lg bg-slate-900/70 border border-slate-800/80 flex items-start gap-2.5">
          <span class="shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded ${tag[1]}">${tag[0]}</span>
          <span class="text-xs text-slate-200 leading-snug">${it.title || ''}</span>
        </div>`;
      }).join('');
    } else {
      nb.textContent = 'Tidak tersedia';
      document.getElementById('news-list').innerHTML = `<div class="text-xs text-slate-500">Berita pasar tidak terjangkau saat ini.</div>`;
    }
    setStatus('Pasar diperbarui');
  } catch (e) {
    showToast('Gagal muat pasar: ' + e.message);
  } finally {
    setRefreshing(false);
  }
}

function jumpSector(name) {
  switchTab('sinyal');
  setTimeout(() => {
    const sel = document.getElementById('sinyal-sector');
    for (const opt of sel.options) {
      if (opt.value.toLowerCase() === name.toLowerCase()) { sel.value = opt.value; break; }
    }
    loadSinyal();
  }, 200);
}

function sigBadge(sig) {
  const s = (sig || 'TAHAN').toUpperCase();
  if (s === 'BELI') return '<span class="px-2 py-0.5 rounded-full text-[11px] font-bold bg-emerald-500/20 text-emerald-300">BELI</span>';
  if (s === 'SIAP BELI') return '<span class="px-2 py-0.5 rounded-full text-[11px] font-bold bg-sky-500/20 text-sky-300">SIAP BELI</span>';
  if (s === 'JUAL') return '<span class="px-2 py-0.5 rounded-full text-[11px] font-bold bg-rose-500/20 text-rose-300">JUAL</span>';
  return `<span class="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-slate-800 text-slate-300">${s}</span>`;
}

async function loadWatchlist() {
  setRefreshing(true);
  try {
    const d = await apiFetch(`/api/watchlist?chat_id=${encodeURIComponent(state.chatId)}&modal=${state.modal}&syariah=${state.syariah}`);
    document.getElementById('badge-watch').textContent = d.total || 0;
    const tb = document.getElementById('watch-tbody');
    if (!d.rows?.length) {
      tb.innerHTML = `<tr><td colspan="11" class="px-4 py-12 text-center text-slate-500">Watchlist kosong. Tambahkan kode saham (mis. BBCA) di atas.</td></tr>`;
      return;
    }
    tb.innerHTML = d.rows.map(r => `<tr onclick="openCek('${esc(r.ticker)}')" class="hover:bg-slate-800/50 transition cursor-pointer">
      <td class="px-4 py-3 mono font-bold text-white">${esc(r.ticker)}</td>
      <td class="px-3 py-3 text-slate-400">${esc(r.sector || '—')}</td>
      <td class="px-3 py-3 text-right mono text-slate-100">${fmtNum(r.price)}</td>
      <td class="px-3 py-3 text-right mono ${(r.ret1d||0)>=0?'text-emerald-400':'text-rose-400'}">${fmtPct(r.ret1d)}</td>
      <td class="px-3 py-3 text-center">${sigBadge(r.signal || r.action)}</td>
      <td class="px-3 py-3 text-right mono text-slate-200">${r.score_pct != null ? Math.round(r.score_pct) : '—'}</td>
      <td class="px-3 py-3 text-right mono text-sky-300">${fmtNum(r.entry)}</td>
      <td class="px-3 py-3 text-right mono text-rose-300">${fmtNum(r.sl)}</td>
      <td class="px-3 py-3 text-right mono text-emerald-300">${fmtNum(r.tp)}</td>
      <td class="px-3 py-3 text-slate-400 max-w-xs truncate">${san(r.reason || '—')}</td>
      <td class="px-3 py-3 text-right" onclick="event.stopPropagation()">
        <button onclick="removeWatch('${esc(r.ticker)}')" class="text-rose-400 hover:text-rose-300 text-xs cursor-pointer">Hapus</button>
      </td>
    </tr>`).join('');
  } finally { setRefreshing(false); }
}

async function addWatchlist() {
  const inp = document.getElementById('watch-add-input');
  const t = inp.value.trim().toUpperCase();
  if (!t) return;
  await apiFetch('/api/watchlist', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ chat_id: state.chatId, ticker: t }) });
  inp.value = '';
  showToast(`${t} ditambahkan ke Watchlist`);
  loadWatchlist();
}
async function removeWatch(t) {
  await apiFetch(`/api/watchlist/${encodeURIComponent(t)}?chat_id=${encodeURIComponent(state.chatId)}`, { method: 'DELETE' });
  showToast(`${t} dihapus dari Watchlist`);
  loadWatchlist();
}

function setSinyalFilter(f) {
  state.sinyalFilter = f;
  document.querySelectorAll('.sf-btn').forEach(b => {
    const active = b.dataset.sf === f;
    b.className = `sf-btn px-3 py-1 rounded-md text-xs font-semibold cursor-pointer ${active ? 'bg-sky-600 text-white' : 'text-slate-400 hover:text-white'}`;
  });
  loadSinyal();
}

async function loadSinyal() {
  syncScanBanner();
  setRefreshing(true);
  try {
    const q = document.getElementById('sinyal-search').value.trim();
    const sec = document.getElementById('sinyal-sector').value;
    const qual = document.getElementById('sinyal-quality').checked;
    const d = await apiFetch(`/api/sinyal?signal=${state.sinyalFilter}&sector=${encodeURIComponent(sec)}&q=${encodeURIComponent(q)}&quality=${qual}&syariah=${state.syariah}`);
    const rows = d.rows || [];
    state.sinyalRows = rows;
    document.getElementById('badge-sinyal').textContent = rows.length;
    const c = d.counts || {};
    document.getElementById('sinyal-meta-bar').textContent = `Snapshot ${(d.meta||{}).trade_date || '—'} · BELI ${c.BELI||0} · TAHAN ${c.TAHAN||0} · JUAL ${c.JUAL||0} · Menampilkan ${rows.length} emiten (klik header tabel untuk urutkan)`;
    const sel = document.getElementById('sinyal-sector');
    if (sel.options.length <= 1 && d.sectors?.length) {
      d.sectors.forEach(s => { const o = document.createElement('option'); o.value = s; o.textContent = s; sel.appendChild(o); });
    }
    renderSinyalRows();
  } finally { setRefreshing(false); }
}

function sortSinyal(col) {
  if (state.sinyalSort.col === col) state.sinyalSort.asc = !state.sinyalSort.asc;
  else state.sinyalSort = { col, asc: col === 'ticker' || col === 'signal' };
  renderSinyalRows();
}

function renderSinyalRows() {
  const { col, asc } = state.sinyalSort;
  document.querySelectorAll('[data-sort]').forEach(el => {
    el.textContent = el.dataset.sort === col ? (asc ? '▲' : '▼') : '';
  });
  const rows = [...state.sinyalRows].sort((a, b) => {
    const va = a[col], vb = b[col];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === 'number' && typeof vb === 'number') return asc ? va - vb : vb - va;
    return asc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
  });
  const tb = document.getElementById('sinyal-tbody');
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="9" class="px-4 py-12 text-center text-slate-500">Tidak ada emiten yang lolos filter. Klik <b>Scan ~944 Emiten BEI</b> atau longgarkan filter.</td></tr>`;
    return;
  }
  tb.innerHTML = rows.map(r => `<tr onclick="openCek('${esc(r.ticker)}')" class="hover:bg-slate-800/50 transition cursor-pointer">
      <td class="px-4 py-2.5 mono font-bold text-white"><span class="inline-flex items-center gap-1.5"><span>${esc(r.ticker)}</span>${r.syariah ? '<span class="px-1 py-0.2 rounded bg-emerald-500/20 text-emerald-300 text-[9px] font-bold">ISSI</span>' : ''}</span></td>
      <td class="px-3 py-2.5 text-slate-300 truncate max-w-[180px]">${esc(r.name || '—')}</td>
      <td class="px-3 py-2.5 text-slate-400">${esc(r.sector || '—')}</td>
      <td class="px-3 py-2.5 text-right mono text-slate-100">${fmtNum(r.price)}</td>
      <td class="px-3 py-2.5 text-right mono ${(r.ret1d||0)>=0?'text-emerald-400':'text-rose-400'}">${fmtPct(r.ret1d)}</td>
      <td class="px-3 py-2.5 text-center">${sigBadge(r.signal)}</td>
      <td class="px-3 py-2.5 text-right mono text-sky-300">${r.rel_pct != null ? Math.round(r.rel_pct) : '—'}</td>
      <td class="px-3 py-2.5 text-right mono text-slate-300">${r.rsi != null ? Number(r.rsi).toFixed(1) : '—'}</td>
      <td class="px-3 py-2.5 text-slate-400 truncate max-w-xs">${san(r.reason || '—')}</td>
    </tr>`).join('');
}

const SPINNER_SVG = `<svg class="w-3.5 h-3.5 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3"></circle><path class="opacity-90" fill="currentColor" d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z"></path></svg>`;

function setScanUiRunning(running, startedAtSec) {
  const btn = document.getElementById('btn-scan-bei');
  const banner = document.getElementById('scan-progress-banner');
  const elap = document.getElementById('scan-elapsed');
  if (running) {
    if (btn) {
      btn.disabled = true;
      btn.classList.add('opacity-75', 'cursor-wait');
      btn.innerHTML = `${SPINNER_SVG}<span>Memindai BEI…</span>`;
    }
    if (banner) banner.classList.remove('hidden');
    window._scanStartedAt = startedAtSec || window._scanStartedAt || (Date.now() / 1000);
    clearInterval(window._scanTick);
    const tick = () => {
      const sec = Math.max(0, Math.floor(Date.now() / 1000 - window._scanStartedAt));
      const mm = String(Math.floor(sec / 60)).padStart(2, '0');
      const ss = String(sec % 60).padStart(2, '0');
      if (elap) elap.textContent = `${mm}:${ss}`;
    };
    tick();
    window._scanTick = setInterval(tick, 1000);
  } else {
    clearInterval(window._scanTick);
    window._scanStartedAt = null;
    if (btn) {
      btn.disabled = false;
      btn.classList.remove('opacity-75', 'cursor-wait');
      btn.innerHTML = `<i data-lucide="zap" class="w-3.5 h-3.5"></i><span>Scan ~944 Emiten BEI</span>`;
      if (window.lucide) lucide.createIcons();
    }
    if (banner) banner.classList.add('hidden');
  }
}

function startScanPoll() {
  clearInterval(window._scanPoll);
  window._scanPoll = setInterval(async () => {
    try {
      const st = await apiFetch('/api/scan/status');
      if (!st.running) {
        clearInterval(window._scanPoll);
        setScanUiRunning(false);
        if (st.error) showToast('Scan gagal: ' + st.error);
        else { showToast('Scan ~944 emiten BEI selesai!'); loadSinyal(); }
      }
    } catch (_) {}
  }, 3000);
}

async function syncScanBanner() {
  try {
    const st = await apiFetch('/api/scan/status');
    if (st.running) {
      setScanUiRunning(true, st.started_at);
      startScanPoll();
    } else {
      setScanUiRunning(false);
    }
  } catch (_) {}
}

async function triggerScan() {
  try {
    const res = await apiFetch('/api/scan', { method: 'POST' });
    setScanUiRunning(true, res.started_at);
    showToast(res.message || 'Scan BEI dimulai di background…');
    startScanPoll();
  } catch (e) {
    setScanUiRunning(false);
    showToast('Gagal memulai scan: ' + e.message);
  }
}

let _paketSimModal = null;
function simPaketModal(amount) {
  _paketSimModal = amount;
  loadPaket();
}

async function loadPaket() {
  setRefreshing(true);
  try {
    const useModal = _paketSimModal || state.modal;
    const d = await apiFetch(`/api/paket?chat_id=${encodeURIComponent(state.chatId)}&modal=${useModal}&syariah=${state.syariah}`);
    const plans = d.plans || [];
    state.lastPaketTicket = d.order_ticket || '';
    document.getElementById('badge-paket').textContent = plans.length;
    document.getElementById('paket-sub').textContent = `Simulasi modal ${fmtRp(useModal)} · otomatis menyesuaikan batas risiko & rezim IHSG.`;
    const wrap = document.getElementById('paket-cards');
    if (!plans.length) {
      wrap.innerHTML = `<div class="col-span-full glass-card rounded-xl p-8 text-center text-sm text-slate-400">${esc(d.reason || 'Belum ada kandidat paket beli yang lolos saringan ketat untuk modal saat ini.')}<div class="text-xs text-slate-500 mt-2">Coba klik preset simulasi modal <b>5 Jt / 10 Jt / 25 Jt</b> di atas atau lihat daftar Kandidat Terdekat di bawah.</div></div>`;
    } else {
      wrap.innerHTML = plans.map(p => `<div class="glass-card rounded-xl p-4 space-y-3 hover:border-sky-500/60 transition">
      <div class="flex items-center justify-between">
        <div>
          <span class="mono text-base font-bold text-white">${esc(p.ticker)}</span>
          <span class="ml-2 text-xs text-slate-400">${esc(p.sector || '')}</span>
        </div>
        <span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-sky-500/20 text-sky-300">Grade ${esc(p.grade || '-')}</span>
      </div>
      <div class="grid grid-cols-3 gap-2 text-xs">
        <div class="p-2 rounded-lg bg-slate-900/80"><div class="text-slate-500 text-[10px]">ENTRY</div><div class="mono font-bold text-sky-300">${fmtNum(p.entry)}</div></div>
        <div class="p-2 rounded-lg bg-slate-900/80"><div class="text-slate-500 text-[10px]">SL</div><div class="mono font-bold text-rose-400">${fmtNum(p.sl)}</div></div>
        <div class="p-2 rounded-lg bg-slate-900/80"><div class="text-slate-500 text-[10px]">TP</div><div class="mono font-bold text-emerald-400">${fmtNum(p.tp)}</div></div>
      </div>
      <div class="flex items-center justify-between text-xs text-slate-300">
        <span>Lot: <b class="mono text-white">${p.lots || 0} lot</b></span>
        <span>Biaya: <b class="mono text-white">${fmtRp(p.buy_cost || p.value)}</b></span>
      </div>
      <div class="flex gap-2 pt-1">
        <button onclick="openCek('${esc(p.ticker)}')" class="flex-1 py-1.5 rounded-lg border border-slate-700 text-xs text-slate-200 hover:bg-slate-800 cursor-pointer">Detail</button>
        <button onclick="prefillExec('${esc(p.ticker)}', ${p.entry||0}, ${p.lots||1})" class="flex-1 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold cursor-pointer">Catat Beli</button>
      </div>
    </div>`).join('');
    }
    const near = d.near_candidates || [];
    const nearCard = document.getElementById('paket-near-card');
    const nearBody = document.getElementById('paket-near-body');
    if (nearCard && nearBody && near.length) {
      nearCard.classList.remove('hidden');
      nearBody.innerHTML = `<table class="w-full text-xs text-left">
        <thead class="text-[10px] uppercase text-slate-500 border-b border-slate-800">
          <tr><th class="py-2 pr-3">Kode</th><th class="py-2 pr-3">Sektor</th><th class="py-2 pr-3 text-right">Harga</th><th class="py-2 pr-3 text-right">Rank%</th><th class="py-2 pr-3 text-right">Min. 1 Lot</th><th class="py-2 text-right">Est. Modal Ideal</th></tr>
        </thead>
        <tbody class="divide-y divide-slate-800/50">${near.map(n => `<tr onclick="openCek('${esc(n.ticker)}')" class="hover:bg-slate-800/50 cursor-pointer">
          <td class="py-2 pr-3 mono font-bold text-sky-300">${esc(n.ticker)}${n.syariah ? ' <span class="px-1 rounded bg-emerald-500/20 text-emerald-300 text-[9px]">ISSI</span>' : ''}</td>
          <td class="py-2 pr-3 text-slate-400">${esc(n.sector || '—')}</td>
          <td class="py-2 pr-3 text-right mono text-slate-100">${fmtRp(n.price)}</td>
          <td class="py-2 pr-3 text-right mono text-emerald-300">${n.rel_pct != null ? Math.round(n.rel_pct) + '%' : '—'}</td>
          <td class="py-2 pr-3 text-right mono">${fmtRp(n.min_lot_cost)}</td>
          <td class="py-2 text-right mono text-amber-300">${fmtRp(n.min_modal_est)}</td>
        </tr>`).join('')}</tbody>
      </table>`;
    } else if (nearCard) {
      nearCard.classList.add('hidden');
    }
  } finally { setRefreshing(false); }
}

function copyPaketOrder() {
  if (!state.lastPaketTicket) return showToast('Belum ada paket untuk disalin');
  navigator.clipboard.writeText(state.lastPaketTicket);
  showToast('Tiket order paket disalin ke clipboard');
}

function prefillExec(ticker, price, lots) {
  switchTab('portofolio');
  document.getElementById('tx-ticker').value = ticker;
  document.getElementById('tx-side').value = 'BELI';
  document.getElementById('tx-price').value = price || '';
  document.getElementById('tx-lots').value = lots || 1;
}

function computeSellAdvice(r) {
  const px = Number(r.price || 0);
  const sl = Number(r.effective_sl || 0);
  const trl = Number(r.trailing_sl || 0);
  const tp1 = Number(r.scale_out_tp1 || 0);
  const tp = Number(r.tp || 0);
  const th = (r.thesis || 'TETAP').toUpperCase();
  if (px > 0 && sl > 0 && px <= sl) return { label: 'CUT LOSS (SL)', cls: 'bg-rose-500/25 text-rose-200 border border-rose-500/40', note: `Harga menyentuh SL (${fmtRp(sl)}) — disiplin keluar.` };
  if (px > 0 && tp > 0 && px >= tp) return { label: 'AMBIL PROFIT (TP)', cls: 'bg-emerald-500/25 text-emerald-200 border border-emerald-500/40', note: `Target TP (${fmtRp(tp)}) tercapai — jual amankan profit.` };
  if (px > 0 && tp1 > 0 && px >= tp1) return { label: 'JUAL SEBAGIAN (TP1)', cls: 'bg-emerald-500/20 text-emerald-300', note: `TP1 (${fmtRp(tp1)}) tercapai — jual 50% atau geser SL ke BEP.` };
  if (th === 'GUGUR') return { label: 'KELUAR (TESIS GUGUR)', cls: 'bg-rose-500/20 text-rose-300', note: 'Tesis posisi gugur — pertimbangkan jual di pantulan.' };
  if (px > 0 && trl > 0 && px <= trl && (r.pnl_net_pct || 0) > 0) return { label: 'TRAILING EXIT', cls: 'bg-amber-500/20 text-amber-300', note: `Tembus trailing stop (${fmtRp(trl)}) — amankan sisa profit.` };
  if (th === 'MELEMAH') return { label: 'HOLD KETAT / SIAP JUAL', cls: 'bg-amber-500/20 text-amber-300', note: `Pantau SL ketat ${fmtRp(sl)} & siap ambil untung dekat ${fmtRp(tp1 || tp)}.` };
  return { label: 'HOLD (TAHAN)', cls: 'bg-sky-500/20 text-sky-300', note: `Tahan selama di atas SL ${fmtRp(sl)}; target jual ${fmtRp(tp1 || tp)}.` };
}

async function loadPortfolio() {
  setRefreshing(true);
  try {
    const d = await apiFetch(`/api/portfolio?chat_id=${encodeURIComponent(state.chatId)}`);
    const rows = d.rows || [];
    state.portoRows = rows;
    document.getElementById('badge-porto').textContent = rows.length;
    const s = d.summary || {};
    document.getElementById('porto-kpis').innerHTML = [
      ['Nilai Pasar', fmtRp(s.total_value), `Modal: ${fmtRp(s.total_cost)}`, 'text-white'],
      ['PnL Belum Realisasi', fmtRp(s.total_pnl_rp), fmtPct(s.total_pnl_pct), (s.total_pnl_rp||0)>=0?'text-emerald-400':'text-rose-400'],
      ['Risiko ke SL', fmtRp(s.total_risk_sl), 'Total potensi rugi ke SL', 'text-amber-400'],
      ['Posisi Aktif', `${rows.length} emiten`, 'Pelacak tesis & saran jual aktif', 'text-sky-400'],
    ].map(([t,v,sub,cls]) => `<div class="glass-card rounded-xl p-4"><div class="text-[11px] uppercase text-slate-400 font-semibold">${t}</div><div class="mono text-xl font-bold mt-1 ${cls}">${v}</div><div class="text-xs text-slate-400 mt-0.5">${sub}</div></div>`).join('');

    const allocEl = document.getElementById('porto-alloc');
    const totVal = Math.max(1, s.total_value || 0);
    if (rows.length && totVal > 1) {
      const colors = ['bg-sky-500', 'bg-emerald-500', 'bg-amber-500', 'bg-purple-500', 'bg-rose-500', 'bg-cyan-500'];
      allocEl.classList.remove('hidden');
      allocEl.innerHTML = `<div class="flex items-center justify-between text-xs"><span class="font-semibold text-slate-300">Alokasi Posisi Portofolio</span><span class="text-slate-400 mono">Total ${fmtRp(s.total_value)}</span></div>
        <div class="h-2.5 w-full rounded-full bg-slate-800 overflow-hidden flex">${rows.map((r, i) => `<div class="${colors[i%colors.length]} h-full" style="width:${((r.value_rp||0)/totVal*100).toFixed(1)}%" title="${esc(r.ticker)}: ${((r.value_rp||0)/totVal*100).toFixed(1)}%"></div>`).join('')}</div>
        <div class="flex flex-wrap gap-3 text-[11px] text-slate-300">${rows.map((r, i) => `<span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full ${colors[i%colors.length]}"></span><b class="mono">${esc(r.ticker)}</b> ${((r.value_rp||0)/totVal*100).toFixed(1)}%</span>`).join('')}</div>`;
    } else {
      allocEl.classList.add('hidden');
    }

    const tb = document.getElementById('porto-tbody');
    if (!rows.length) {
      tb.innerHTML = `<tr><td colspan="11" class="px-4 py-12 text-center text-slate-500">Belum ada posisi terbuka. Catat eksekusi BELI manual Stockbit di form atas.</td></tr>`;
      return;
    }
    tb.innerHTML = rows.map(r => {
      const th = r.thesis || 'TETAP';
      const thCls = th === 'GUGUR' ? 'bg-rose-500/20 text-rose-300' : (th === 'MELEMAH' ? 'bg-amber-500/20 text-amber-300' : (th === 'MENGUAT' ? 'bg-emerald-500/20 text-emerald-300' : 'bg-slate-800 text-slate-300'));
      const adv = computeSellAdvice(r);
      return `<tr onclick="openCek('${esc(r.ticker)}')" class="hover:bg-slate-800/50 cursor-pointer">
        <td class="px-4 py-3 mono font-bold text-white">${esc(r.ticker)}</td>
        <td class="px-3 py-3 text-right mono">${r.lots}</td>
        <td class="px-3 py-3 text-right mono">${fmtNum(r.avg_price)}</td>
        <td class="px-3 py-3 text-right mono">${fmtNum(r.price)}</td>
        <td class="px-3 py-3 text-right mono ${(r.pnl_net_pct||0)>=0?'text-emerald-400':'text-rose-400'}">${fmtPct(r.pnl_net_pct)}</td>
        <td class="px-3 py-3 text-right mono text-rose-300">${fmtNum(r.effective_sl)}${r.trailing_sl ? `<div class="text-[10px] text-slate-500">Trl ${fmtNum(r.trailing_sl)}</div>` : ''}</td>
        <td class="px-3 py-3 text-right mono text-emerald-300">${r.scale_out_tp1 ? fmtNum(r.scale_out_tp1) : '—'} / ${r.tp ? fmtNum(r.tp) : '—'}</td>
        <td class="px-3 py-3 text-center"><span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${adv.cls}" title="${adv.note}">${adv.label}</span></td>
        <td class="px-3 py-3 text-center"><span class="px-2 py-0.5 rounded-full text-[11px] font-bold ${thCls}">${th}</span></td>
        <td class="px-3 py-3 text-slate-400 truncate max-w-xs" title="${adv.note}">${(r.reasons||[]).slice(0,2).join('; ') || adv.note}</td>
        <td class="px-3 py-3 text-right" onclick="event.stopPropagation()">
          <button onclick="prefillSell('${esc(r.ticker)}', ${r.price||r.avg_price||0}, ${r.lots||1})" class="px-2.5 py-1 rounded-md bg-rose-600/80 hover:bg-rose-500 text-white text-[11px] font-semibold cursor-pointer">Jual</button>
        </td>
      </tr>`;
    }).join('');
    loadPortfolioBenchmark();
  } finally { setRefreshing(false); }
}

async function loadPortfolioBenchmark() {
  const card = document.getElementById('porto-bench-card');
  const sumEl = document.getElementById('porto-bench-summary');
  const el = document.getElementById('porto-bench-chart');
  if (!card || !el) return;
  try {
    const d = await apiFetch(`/api/portfolio/benchmark?chat_id=${encodeURIComponent(state.chatId)}&days=45`);
    if (!d.ok || !(d.series || []).length) {
      card.classList.add('hidden');
      return;
    }
    card.classList.remove('hidden');
    if (sumEl) {
      sumEl.innerHTML = `Portofolio <b class="${(d.portfolio_ret_pct||0)>=0?'text-emerald-400':'text-rose-400'}">${fmtPct(d.portfolio_ret_pct)}</b> vs IHSG <b class="text-slate-300">${fmtPct(d.ihsg_ret_pct)}</b> · Alpha <b class="${(d.alpha_pct||0)>=0?'text-emerald-400':'text-amber-400'}">${fmtPct(d.alpha_pct)}</b>`;
    }
    el.innerHTML = '';
    if (!window.LightweightCharts) return;
    const chart = LightweightCharts.createChart(el, {
      width: el.clientWidth,
      height: 210,
      layout: { background: { color: '#060911' }, textColor: '#94a3b8' },
      grid: { vertLines: { color: '#111827' }, horzLines: { color: '#111827' } },
      rightPriceScale: { borderColor: '#1e293b' },
      timeScale: { borderColor: '#1e293b' },
    });
    const sP = chart.addLineSeries({ color: '#10b981', lineWidth: 2, title: 'Portofolio %' });
    const sI = chart.addLineSeries({ color: '#94a3b8', lineWidth: 1.5, lineStyle: 2, title: 'IHSG %' });
    sP.setData(d.series.map(pt => ({ time: pt.time, value: pt.portfolio_pct })));
    sI.setData(d.series.map(pt => ({ time: pt.time, value: pt.ihsg_pct })));
    chart.timeScale().fitContent();
    new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth })).observe(el);
  } catch (_) {
    card.classList.add('hidden');
  }
}

function openImportTxModal() {
  document.getElementById('import-tx-modal')?.classList.remove('hidden');
  document.getElementById('import-tx-result')?.classList.add('hidden');
  document.getElementById('import-tx-text')?.focus();
}
function closeImportTxModal() {
  document.getElementById('import-tx-modal')?.classList.add('hidden');
}
async function submitBulkImport() {
  const raw_text = document.getElementById('import-tx-text')?.value || '';
  const resEl = document.getElementById('import-tx-result');
  if (!raw_text.trim()) return showToast('Tempel minimal 1 baris transaksi');
  try {
    const d = await apiFetch('/api/execution/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: state.chatId, raw_text }),
    });
    if (resEl) {
      resEl.classList.remove('hidden');
      resEl.className = `text-xs p-2.5 rounded-lg ${d.imported > 0 ? 'bg-emerald-500/15 text-emerald-300' : 'bg-rose-500/15 text-rose-300'}`;
      resEl.innerHTML = `Berhasil mengimpor <b>${d.imported}</b> transaksi.${(d.errors || []).length ? '<br>' + d.errors.slice(0, 4).map(esc).join('<br>') : ''}`;
    }
    if (d.imported > 0) {
      document.getElementById('import-tx-text').value = '';
      showToast(`${d.imported} transaksi berhasil diimpor`);
      loadPortfolio();
    }
  } catch (e) {
    showToast('Gagal impor: ' + e.message);
  }
}

function prefillSell(ticker, price, lots) {
  document.getElementById('tx-ticker').value = ticker;
  document.getElementById('tx-side').value = 'JUAL';
  document.getElementById('tx-price').value = price || '';
  document.getElementById('tx-lots').value = lots || 1;
  document.getElementById('tx-notes').focus();
  showToast(`Form JUAL ${ticker} (${lots} lot) siap disimpan`);
}

async function submitExecution() {
  const ticker = document.getElementById('tx-ticker').value.trim().toUpperCase();
  const side = document.getElementById('tx-side').value;
  const price = Number(document.getElementById('tx-price').value);
  const lots = Number(document.getElementById('tx-lots').value);
  const notes = document.getElementById('tx-notes').value.trim();
  if (!ticker || !price || !lots) return showToast('Lengkapi kode, harga, dan lot');
  try {
    const res = await apiFetch('/api/execution', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ chat_id: state.chatId, ticker, side, price, lots, notes }),
    });
    showToast(res.message || 'Eksekusi tercatat');
    document.getElementById('tx-ticker').value = '';
    document.getElementById('tx-price').value = '';
    document.getElementById('tx-lots').value = '';
    loadPortfolio();
  } catch (e) { showToast(e.message); }
}

async function loadJurnal() {
  setRefreshing(true);
  try {
    const d = await apiFetch(`/api/jurnal?chat_id=${encodeURIComponent(state.chatId)}`);
    const k = d.kpis || {};
    document.getElementById('jurnal-kpis').innerHTML = [
      ['Total Eksekusi', `${k.n_exec || 0}`, `Beli ${k.n_buys || 0} · Jual ${k.n_sells || 0}`, 'text-white'],
      ['Win Rate', k.win_rate_pct != null ? `${Number(k.win_rate_pct).toFixed(1)}%` : 'Belum ada close', `${k.n_closed || 0} transaksi selesai`, 'text-sky-400'],
      ['PnL Realisasi', fmtRp(k.total_net_pnl_rp || 0), k.profit_factor != null ? `PF ${Number(k.profit_factor).toFixed(2)}x` : 'Setelah fee BEI', (k.total_net_pnl_rp || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'],
      ['Alpha vs IHSG', k.mean_alpha_pct != null ? fmtPct(k.mean_alpha_pct) : 'Belum ada close', k.avg_slippage_pct != null ? `Slippage ${fmtPct(k.avg_slippage_pct)}` : 'Rata-rata per trade', 'text-amber-400'],
    ].map(([t,v,sub,cls]) => `<div class="glass-card rounded-xl p-4"><div class="text-[11px] uppercase text-slate-400 font-semibold">${t}</div><div class="mono text-xl font-bold mt-1 ${cls}">${v}</div><div class="text-xs text-slate-400 mt-0.5">${sub}</div></div>`).join('');

    const execs = d.execs || [];
    const tb = document.getElementById('jurnal-tbody');
    if (!execs.length) {
      tb.innerHTML = `<tr><td colspan="8" class="px-4 py-8 text-center text-slate-500">Belum ada eksekusi tercatat. Catat transaksi di tab Portofolio.</td></tr>`;
    } else {
      tb.innerHTML = execs.map(ex => {
        const val = (ex.exec_price || 0) * (ex.lots || 0) * 100;
        const isBuy = (ex.side || '').toUpperCase() === 'BELI';
        return `<tr onclick="openCek('${ex.ticker}')" class="hover:bg-slate-800/50 cursor-pointer">
          <td class="px-4 py-2.5 mono text-slate-400">${ex.trade_date || '—'}</td>
          <td class="px-3 py-2.5 mono font-bold text-white">${ex.ticker}</td>
          <td class="px-3 py-2.5 text-center"><span class="px-2 py-0.5 rounded-full text-[11px] font-bold ${isBuy ? 'bg-emerald-500/20 text-emerald-300' : 'bg-rose-500/20 text-rose-300'}">${ex.side}</span></td>
          <td class="px-3 py-2.5 text-right mono">${ex.lots}</td>
          <td class="px-3 py-2.5 text-right mono">${fmtRp(ex.exec_price)}</td>
          <td class="px-3 py-2.5 text-right mono text-slate-200">${fmtRp(val)}</td>
          <td class="px-3 py-2.5 text-right mono ${(ex.net_pnl||0)>=0?'text-emerald-400':'text-rose-400'}">${ex.net_pnl != null ? fmtRp(ex.net_pnl) : '—'}</td>
          <td class="px-3 py-2.5 text-slate-400">${ex.source || '—'}</td>
        </tr>`;
      }).join('');
    }
    const strip = s => {
      const ta = document.createElement('textarea');
      ta.innerHTML = (s || '').replace(/<[^>]+>/g, '');
      return ta.value;
    };
    document.getElementById('jurnal-content').textContent = strip(d.summary_text) + (d.history_text ? '\n\n' + strip(d.history_text) : '');
  } finally { setRefreshing(false); }
}

function openCek(ticker) {
  document.getElementById('cek-input').value = ticker;
  switchTab('cek');
  loadCek();
}

const fmtCompactRp = (v) => {
  if (v == null || !isFinite(v) || v <= 0) return '—';
  if (v >= 1e12) return `Rp${(v / 1e12).toFixed(2)} T`;
  if (v >= 1e9) return `Rp${(v / 1e9).toFixed(1)} M`;
  if (v >= 1e6) return `Rp${(v / 1e6).toFixed(0)} Jt`;
  return fmtRp(v);
};

async function loadCek() {
  const ticker = document.getElementById('cek-input').value.trim().toUpperCase();
  const period = document.getElementById('cek-period').value;
  if (!ticker) return;
  setRefreshing(true);
  const cBtn = document.getElementById('btn-cek-run');
  const cSpin = document.getElementById('cek-btn-spin');
  const cLbl = document.getElementById('cek-btn-lbl');
  if (cBtn) cBtn.disabled = true;
  if (cSpin) cSpin.classList.remove('hidden');
  if (cLbl) cLbl.textContent = `Menganalisis ${ticker}…`;
  setStatus(`Menganalisis ${ticker}…`);
  try {
    const d = await apiFetch(`/api/cek/${encodeURIComponent(ticker)}?chat_id=${encodeURIComponent(state.chatId)}&modal=${state.modal}&period=${period}`);
    state.lastCek = d;
    const snap = (d.res || {}).snap || {};
    const cek = d.cek || {};
    const ps = d.powerscore || {};
    const plan = d.plan || {};
    const pd = d.plan_display || {};
    const fund = d.fund || {};
    const news = d.news || {};

    document.getElementById('cek-title').textContent = `${d.ticker} — ${d.name || d.ticker}`;
    document.getElementById('cek-meta').textContent = `${d.sector || '—'} (${d.board || '—'}) · ${d.syariah ? 'SYARIAH (ISSI)' : 'Non-ISSI'} · PowerScore: ${ps.rating || '—'} (${ps.pct ?? 0}%)`;
    document.getElementById('cek-price').textContent = fmtRp(snap.close);
    document.getElementById('cek-ret').textContent = fmtPct(snap.ret1d);
    document.getElementById('cek-ret').className = `mono text-xs ${(snap.ret1d||0)>=0?'text-emerald-400':'text-rose-400'}`;

    const v = (cek.verdict || 'TUNGGU').toUpperCase();
    const vp = document.getElementById('cek-verdict-pill');
    vp.textContent = `${v}${cek.points != null ? ` (${cek.points} pt)` : ''}`;
    vp.className = `px-3.5 py-1.5 rounded-full text-xs font-bold ${v==='LAYAK'?'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40':(v==='HINDARI'?'bg-rose-500/20 text-rose-300 border border-rose-500/40':'bg-amber-500/20 text-amber-300 border border-amber-500/40')}`;

    // Visual PowerScore Gauge + Accent Top Glow
    const psWrap = document.getElementById('cek-ps-gauge-wrap');
    const psBar = document.getElementById('cek-ps-bar');
    const psLbl = document.getElementById('cek-ps-lbl');
    const glow = document.getElementById('cek-banner-glow');
    if (psWrap && psBar && psLbl) {
      const pct = Number(ps.pct ?? 0);
      const norm = Math.max(4, Math.min(100, Math.round((pct + 100) / 2)));
      psWrap.classList.remove('hidden');
      psBar.style.width = `${norm}%`;
      psBar.className = `h-full rounded-full transition-all duration-500 ${pct >= 30 ? 'bg-gradient-to-r from-sky-400 to-emerald-400' : (pct <= -20 ? 'bg-gradient-to-r from-rose-500 to-amber-500' : 'bg-gradient-to-r from-amber-400 to-sky-400')}`;
      psLbl.textContent = `${ps.rating || 'NETRAL'} (${pct >= 0 ? '+' : ''}${Math.round(pct)}%)`;
    }
    if (glow) {
      glow.className = `absolute top-0 inset-x-0 h-[2px] ${v==='LAYAK'?'bg-gradient-to-r from-emerald-400 via-sky-400 to-transparent':(v==='HINDARI'?'bg-gradient-to-r from-rose-500 via-amber-500 to-transparent':'bg-gradient-to-r from-amber-400 via-sky-400 to-transparent')}`;
    }
    pushRecentTicker(d.ticker);

    document.getElementById('cek-plan-grid').innerHTML = [
      ['Entry', pd.entry || fmtRp(plan.entry), pd.entry_sub || 'Limit beli', 'text-sky-400'],
      ['Stop-Loss', pd.sl || fmtRp(plan.sl), pd.sl_sub || 'Batas rugi', 'text-rose-400'],
      ['Target TP', pd.tp || fmtRp(plan.tp), pd.tp_sub || 'Target profit', 'text-emerald-400'],
      ['Lot', pd.lots || '0 lot', pd.lots_sub || 'Ukuran posisi', pd.ok ? 'text-white' : 'text-amber-400'],
      ['Est. Biaya', pd.cost || '—', pd.cost_sub || 'Termasuk fee', 'text-white'],
      ['Risiko ke SL', pd.risk || '—', pd.risk_sub || 'Rugi maks SL', 'text-amber-400'],
    ].map(([t,val,sub,cls]) => `<div class="glass-card rounded-xl p-3"><div class="text-[10px] uppercase text-slate-400 font-semibold">${t}</div><div class="mono text-sm font-bold mt-1 ${cls}">${val}</div><div class="text-[11px] text-slate-400 mt-0.5 truncate" title="${String(sub).replace(/"/g, '&quot;')}">${sub}</div></div>`).join('');

    // Warning / Veto Banner
    const warnEl = document.getElementById('cek-warn-banner');
    const warnLines = [];
    if (plan.skip_reason) warnLines.push(`<b>Sizing 0 lot:</b> ${plan.skip_reason}`);
    (plan.vetoes || []).forEach(x => warnLines.push(`<b>Veto:</b> ${x}`));
    (plan.warnings || []).slice(0, 3).forEach(x => {
      if (!plan.skip_reason || !plan.skip_reason.includes(x)) warnLines.push(x);
    });
    if (warnLines.length) {
      warnEl.classList.remove('hidden');
      warnEl.innerHTML = warnLines.map(w => `<div>• ${san(w)}</div>`).join('');
    } else {
      warnEl.classList.add('hidden');
      warnEl.innerHTML = '';
    }

    // Corporate Action Override, Bank Ratios & Auto News Corp-Action Alerts
    const corpEl = document.getElementById('cek-corp-banner');
    const ov = fund.corp_override;
    const newsAlerts = d.news_corp_alerts || [];
    const corpChunks = [];
    if (ov && (ov.corp_action_flags?.length || ov.bank_ratios)) {
      const br = ov.bank_ratios || {};
      const brPills = Object.entries({
        NIM: br.nim_pct, 'NPF Gross': br.npf_gross_pct, 'NPF Net': br.npf_net_pct,
        CAR: br.car_pct, CASA: br.casa_pct, ROE: br.roe_pct,
      }).filter(([, val]) => val != null).map(([k, val]) => `<span class="px-2 py-0.5 rounded bg-slate-900/90 border border-slate-700 mono text-[11px] text-sky-300">${k}: <b>${val}%</b></span>`).join('');
      const flgHtml = (ov.corp_action_flags || []).map(f => `<div>⚠️ <b>Aksi Korporasi / Risiko:</b> ${f}</div>`).join('');
      corpChunks.push(`<div class="flex flex-wrap items-center justify-between gap-2"><span class="font-bold text-rose-300">Catatan Khusus Emiten (${ov.as_of || 'Lokal'})</span><span class="text-[11px] text-slate-400">${ov.source || ''}</span></div>${flgHtml}${brPills ? `<div class="flex flex-wrap gap-1.5 pt-1">${brPills}</div>` : ''}`);
    }
    if (newsAlerts.length) {
      corpChunks.push(newsAlerts.map(a => `<div class="flex items-start gap-2 pt-1 border-t border-rose-500/20"><span class="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-bold ${a.severity==='danger'?'bg-rose-500/25 text-rose-200':'bg-amber-500/25 text-amber-200'}">${a.category}</span><div><div class="text-slate-100">${a.headline}</div><div class="text-[11px] text-slate-400">${a.note}</div></div></div>`).join(''));
    }
    if (corpChunks.length) {
      corpEl.classList.remove('hidden');
      corpEl.innerHTML = corpChunks.join('');
    } else {
      corpEl.classList.add('hidden');
      corpEl.innerHTML = '';
    }

    // Reset Live Price Pill & Render Pre-Buy Checklist
    const lpEl = document.getElementById('cek-live-price');
    if (lpEl) lpEl.classList.add('hidden');
    window._prebuyList = (d.prebuy_checklist || []).map(x => ({ ...x }));
    renderPrebuyChecklist();

    // 8 Technical & Fundamental Metric Strip
    const liqRp = (snap.vol_avg20 && snap.close) ? snap.vol_avg20 * snap.close : null;
    const rsi = snap.rsi;
    const rsiSub = rsi == null ? '—' : (rsi >= 70 ? 'Overbought' : (rsi <= 30 ? 'Oversold' : 'Netral/Sehat'));
    const foren = fund.forensic || {};
    const qtr = fund.quarterly || {};
    const newsCov = (news.engine_coverage || {}).summary || '';
    document.getElementById('cek-metrics-grid').innerHTML = [
      ['RSI (14)', rsi != null ? Number(rsi).toFixed(1) : '—', rsiSub, rsi >= 70 ? 'text-rose-400' : (rsi <= 30 ? 'text-emerald-400' : 'text-sky-300')],
      ['MA20 / MA50', snap.sma20 ? `${fmtNum(snap.sma20)} / ${fmtNum(snap.sma50)}` : '—', snap.sma200 ? `MA200 ${fmtNum(snap.sma200)}` : 'MA200 NA', 'text-white'],
      ['Return 5H / 20H', fmtPct(snap.ret5d), `20H ${fmtPct(snap.ret20d)}`, (snap.ret20d || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'],
      ['Likuiditas 20H', fmtCompactRp(liqRp), snap.vol_avg20 ? `Vol ${fmtNum(snap.vol_avg20)}` : '—', liqRp && liqRp < 2e9 ? 'text-rose-400' : 'text-emerald-400'],
      ['Fundamental', fund.pct != null ? `${Math.round(fund.pct)}%` : '—', fund.ev_ebitda != null ? `EV/EBITDA ${Number(fund.ev_ebitda).toFixed(1)}x` : (fund.label || '—'), (fund.pct || 0) >= 60 ? 'text-emerald-400' : 'text-amber-400'],
      ['FCF & Kas', fund.fcf_yield != null ? `${Number(fund.fcf_yield).toFixed(1)}%` : '—', foren.ocf_to_ni != null ? `OCF/NI ${Number(foren.ocf_to_ni).toFixed(2)}x` : 'Forensik kas', (fund.fcf_yield || 0) > 0 ? 'text-emerald-400' : 'text-slate-300'],
      ['Tumbuh 4Q', qtr.profit_growth_pct != null ? fmtPct(qtr.profit_growth_pct) : '—', qtr.rev_growth_pct != null ? `Omzet ${fmtPct(qtr.rev_growth_pct)}` : 'Kuartalan NA', (qtr.profit_growth_pct || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'],
      ['Sentimen Berita', news.n ? `${news.label} (${Math.round(news.pct || 0)}%)` : '—', news.n ? `${news.n} berita · ${newsCov}` : 'Tidak ada berita', news.label === 'POSITIF' ? 'text-emerald-400' : (news.label === 'NEGATIF' ? 'text-rose-400' : 'text-amber-400')],
    ].map(([t,val,sub,cls]) => `<div class="rounded-lg bg-slate-900/75 border border-slate-800/80 px-2.5 py-2"><div class="text-[10px] uppercase text-slate-500 font-semibold truncate">${t}</div><div class="mono text-xs font-bold mt-0.5 truncate ${cls}">${val}</div><div class="text-[10px] text-slate-400 truncate" title="${sub}">${sub}</div></div>`).join('');

    // Fundamental Notes
    document.getElementById('cek-filing-note').textContent = fund.filing_note || '';
    const fNotes = fund.notes || [];
    document.getElementById('cek-fund-notes').innerHTML = fNotes.length
      ? fNotes.map(n => `<div class="px-2.5 py-1.5 rounded-lg bg-slate-900/70 border border-slate-800/70">${n}</div>`).join('')
      : `<div class="text-slate-500">Data fundamental rinci tidak tersedia untuk emiten ini.</div>`;

    // Peer Se-Sektor Chips
    const peers = d.peers || [];
    document.getElementById('cek-peers').innerHTML = peers.length
      ? `<span class="text-slate-500 mr-1">Peer:</span>` + peers.map(p => `<button onclick="openCek('${p}')" class="px-2 py-0.5 rounded bg-slate-800 hover:bg-sky-600/40 text-sky-300 mono cursor-pointer transition">${p}</button>`).join('')
      : '';

    // Berita Emiten
    document.getElementById('cek-news-summary').textContent = news.n
      ? `${news.label} (${Math.round(news.pct || 0)}%) · ${news.n} berita · ${(news.engine_coverage || {}).summary || ''}`
      : 'Tidak ada berita';
    const nList = news.details || [];
    document.getElementById('cek-news-list').innerHTML = nList.length
      ? nList.slice(0, 8).map(it => {
          const sc = it.sent || 0;
          const tag = sc >= 0.25 ? ['POSITIF', 'bg-emerald-500/15 text-emerald-400'] : (sc <= -0.25 ? ['NEGATIF', 'bg-rose-500/15 text-rose-400'] : ['NETRAL', 'bg-slate-800 text-slate-400']);
          return `<div class="p-2.5 rounded-lg bg-slate-900/70 border border-slate-800/80 flex items-start gap-2.5"><span class="shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded ${tag[1]}">${tag[0]}</span><span class="text-slate-200 leading-snug">${it.title || ''}</span></div>`;
        }).join('')
      : `<div class="text-slate-500">Tidak ada berita terbaru untuk ${d.ticker}.</div>`;

    // Prefill Kalkulator Sizing Interaktif (pastikan SL selalu valid di bawah Entry)
    prefillCalcFromCek(d);

    document.getElementById('cek-bull').innerHTML = (cek.bull_points?.length ? cek.bull_points : ['Belum ada poin kuat']).map(x => `<li>${san(x)}</li>`).join('');
    document.getElementById('cek-bear').innerHTML = (cek.bear_points?.length ? cek.bear_points : ['Tidak ada risiko mayor']).map(x => `<li>${san(x)}</li>`).join('');
    document.getElementById('cek-items').innerHTML = (cek.items || []).map(it => {
      const c = it.status === 'ok' ? 'text-emerald-400' : (it.status === 'bad' ? 'text-rose-400' : (it.status === 'warn' ? 'text-amber-400' : 'text-slate-500'));
      return `<div class="flex justify-between gap-2 border-b border-slate-800/60 pb-1.5"><span class="text-slate-400">${it.label}</span><span class="font-semibold text-right ${c}">${it.headline}</span></div>`;
    }).join('');

    renderTvChart(d.candles || [], plan, d.poc_60d);
    renderSimpleAndFairValue(d);
    renderHoldingAdviceForCek(d.ticker);
    document.getElementById('bt-body').innerHTML = 'Klik <b>Hitung 5Th</b> untuk melihat akurasi historis sinyal BELI (horizon 20 hari, net fee BEI 0,40%).';
    loadStockbitLive(d.ticker);
    setStatus(`Cek ${d.ticker} selesai (${v})`);
  } catch (e) {
    showToast('Gagal cek emiten: ' + e.message);
  } finally {
    setRefreshing(false);
    const cBtn = document.getElementById('btn-cek-run');
    const cSpin = document.getElementById('cek-btn-spin');
    const cLbl = document.getElementById('cek-btn-lbl');
    if (cBtn) cBtn.disabled = false;
    if (cSpin) cSpin.classList.add('hidden');
    if (cLbl) cLbl.textContent = 'Analisis Lengkap';
  }
}

let _cekCompact = false;
function toggleCekDetailMode() {
  _cekCompact = !_cekCompact;
  document.querySelectorAll('.cek-detail-only').forEach(el => el.classList.toggle('hidden', _cekCompact));
  const btn = document.getElementById('btn-cek-mode');
  if (btn) btn.textContent = _cekCompact ? 'Mode: Ringkas (Pemula)' : 'Mode: Detail Pro';
}

function _toneCls(tone) {
  if (tone === 'emerald') return { text: 'text-emerald-300', border: 'border-emerald-500/35 bg-emerald-500/10', pill: 'bg-emerald-500/20 text-emerald-300' };
  if (tone === 'rose') return { text: 'text-rose-300', border: 'border-rose-500/35 bg-rose-500/10', pill: 'bg-rose-500/20 text-rose-300' };
  if (tone === 'amber') return { text: 'text-amber-300', border: 'border-amber-500/35 bg-amber-500/10', pill: 'bg-amber-500/20 text-amber-300' };
  if (tone === 'sky') return { text: 'text-sky-300', border: 'border-sky-500/35 bg-sky-500/10', pill: 'bg-sky-500/20 text-sky-300' };
  return { text: 'text-slate-300', border: 'border-slate-700 bg-slate-900/70', pill: 'bg-slate-800 text-slate-300' };
}

function renderSimpleAndFairValue(d) {
  // 1. Ringkasan 4 Pilar Bahasa Sederhana
  const ss = d.simple_summary;
  const card = document.getElementById('cek-simple-card');
  if (ss && card) {
    card.classList.remove('hidden');
    const act = ss.action || {};
    const ac = _toneCls(act.tone);
    document.getElementById('sim-action-head').className = `text-base font-bold ${ac.text}`;
    document.getElementById('sim-action-head').textContent = act.headline || '—';
    document.getElementById('sim-action-det').textContent = act.detail || '';
    const pillars = [
      ['1. Tren Harga', ss.trend],
      ['2. Bisnis & Wajar', ss.business],
      ['3. Risiko & Sizing', ss.risk],
      ['4. Aliran Bandar', { status: 'Menunggu MCP…', tone: 'slate', text: 'Mengambil data broker summary live…' }],
    ];
    document.getElementById('sim-pillars').innerHTML = pillars.map(([title, p], idx) => {
      const c = _toneCls(p?.tone);
      return `<div id="sim-p-${idx}" class="rounded-lg border p-2.5 ${c.border}">
        <div class="flex items-center justify-between gap-1"><span class="text-[10px] uppercase text-slate-400 font-semibold">${title}</span><span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${c.pill}">${p?.status || '—'}</span></div>
        <div class="text-[11px] text-slate-200 mt-1 leading-snug">${p?.text || '—'}</div>
      </div>`;
    }).join('');
  }

  // 2. Estimasi Nilai Wajar (Fair Value & Margin of Safety)
  const fv = d.fair_value || {};
  const fvBadge = document.getElementById('fv-badge');
  const fvBody = document.getElementById('fv-body');
  if (!fv.available) {
    fvBadge.textContent = 'Tidak tersedia';
    fvBadge.className = 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-400';
    fvBody.innerHTML = `<span class="text-slate-500">${esc(fv.reason || 'Data tidak mencukupi.')}</span>`;
  } else {
    const fc = _toneCls(fv.tone);
    fvBadge.textContent = `${fv.label} (${fv.mos_pct >= 0 ? '+' : ''}${fv.mos_pct}%)`;
    fvBadge.className = `px-2 py-0.5 rounded-full text-[10px] font-bold ${fc.pill}`;
    fvBody.innerHTML = `<div class="flex items-baseline justify-between gap-2 mb-1">
        <span class="mono text-lg font-bold ${fc.text}">${fmtRp(fv.fair_value)}</span>
        <span class="text-[10px] text-slate-400">${fv.method || ''}${fv.haircut_pct ? ` · haircut -${fv.haircut_pct}%` : ''}</span>
      </div>
      <div class="text-[11px] text-slate-300 leading-snug">${fv.note || ''}</div>`;
  }
}

function renderPrebuyChecklist() {
  const card = document.getElementById('cek-prebuy-card');
  const wrap = document.getElementById('cek-prebuy-checklist');
  const scEl = document.getElementById('prebuy-score');
  const list = window._prebuyList || [];
  if (!card || !wrap || !list.length) return;
  card.classList.remove('hidden');
  const okCount = list.filter(x => x.status === 'ok').length;
  const badCount = list.filter(x => x.status === 'bad').length;
  scEl.textContent = badCount ? `${okCount}/${list.length} Lolos · ${badCount} Risiko` : `${okCount}/${list.length} Lolos`;
  scEl.className = `px-2 py-0.5 rounded-full text-[10px] font-bold ${badCount ? 'bg-rose-500/20 text-rose-300' : (okCount === list.length ? 'bg-emerald-500/20 text-emerald-300' : 'bg-amber-500/20 text-amber-300')}`;
  wrap.innerHTML = list.map(it => {
    const ic = it.status === 'ok' ? '✅' : (it.status === 'bad' ? '❌' : (it.status === 'warn' ? '⚠️' : '⏳'));
    const cls = it.status === 'ok' ? 'border-emerald-500/30 bg-emerald-500/5' : (it.status === 'bad' ? 'border-rose-500/35 bg-rose-500/10' : 'border-slate-700 bg-slate-900/70');
    return `<div class="rounded-lg border p-2.5 ${cls}">
      <div class="text-[11px] font-semibold text-slate-100">${ic} ${it.label}</div>
      <div class="text-[10px] text-slate-400 mt-0.5 leading-snug">${it.detail}</div>
    </div>`;
  }).join('');
}

function renderHoldingAdviceForCek(ticker) {
  const el = document.getElementById('cek-holding-card');
  if (!el) return;
  const pos = (state.portoRows || []).find(r => r.ticker === ticker);
  if (!pos) {
    el.classList.add('hidden');
    el.innerHTML = '';
    return;
  }
  const adv = computeSellAdvice(pos);
  el.classList.remove('hidden');
  el.innerHTML = `<div class="flex flex-wrap items-center justify-between gap-3">
    <div class="space-y-0.5">
      <div class="flex items-center gap-2">
        <span class="text-[10px] uppercase tracking-wider font-bold text-emerald-400">Posisi Anda di ${pos.ticker} (${pos.lots} lot @ Avg ${fmtRp(pos.avg_price)})</span>
        <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${adv.cls}">${adv.label}</span>
      </div>
      <div class="text-xs text-slate-200">PnL Net: <b class="mono ${(pos.pnl_net_pct||0)>=0?'text-emerald-400':'text-rose-400'}">${fmtRp(pos.pnl_rp)} (${fmtPct(pos.pnl_net_pct)})</b> · Batas Cut-Loss: <b class="mono text-rose-300">${fmtRp(pos.effective_sl)}</b> · Target Jual TP1/TP: <b class="mono text-emerald-300">${fmtRp(pos.scale_out_tp1)} / ${fmtRp(pos.tp)}</b></div>
      <div class="text-[11px] text-slate-400">${adv.note}</div>
    </div>
    <button onclick="switchTab('portofolio');prefillSell('${pos.ticker}', ${pos.price||pos.avg_price||0}, ${pos.lots||1})" class="px-3.5 py-2 rounded-lg bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold cursor-pointer shrink-0">Catat Jual ${pos.ticker}</button>
  </div>`;
}

function updateBandarConfluence(sb) {
  const pill = document.getElementById('sim-confluence-pill');
  const p3 = document.getElementById('sim-p-3');
  if (!pill || !p3) return;
  // Tampilkan Harga Live Stockbit (tanpa delay 15 menit) bila tersedia
  const lpEl = document.getElementById('cek-live-price');
  if (lpEl && sb?.available && sb.quote?.price) {
    const qpx = Number(sb.quote.price);
    const qpct = sb.quote.percentage != null ? Number(sb.quote.percentage) : null;
    lpEl.textContent = `Live Stockbit: ${fmtRp(qpx)}${qpct != null ? ` (${fmtPct(qpct)})` : ''}`;
    lpEl.classList.remove('hidden');
    if (sb.quote.name && state.lastCek?.ticker) {
      const curTitle = document.getElementById('cek-title')?.textContent || '';
      if (curTitle.includes('(') && !curTitle.includes(')')) {
        document.getElementById('cek-title').textContent = `${state.lastCek.ticker} — ${sb.quote.name}`;
      }
    }
  }
  if (!sb?.available) {
    pill.textContent = 'Konfluensi Bandar: MCP Off';
    pill.className = 'px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-400 shrink-0';
    p3.innerHTML = `<div class="flex items-center justify-between gap-1"><span class="text-[10px] uppercase text-slate-400 font-semibold">4. Aliran Bandar</span><span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-400">Off</span></div><div class="text-[11px] text-slate-400 mt-1">Stockbit MCP tidak aktif.</div>`;
    if (window._prebuyList?.[3]) { window._prebuyList[3].status = 'warn'; window._prebuyList[3].detail = 'Stockbit MCP nonaktif — cek orderbook manual.'; renderPrebuyChecklist(); }
    return;
  }
  const snap = (state.lastCek?.res || {}).snap || {};
  const trendUp = snap.close && snap.sma20 && snap.close > snap.sma20;
  const st1 = sb.bandar_1d?.status || 'Netral';
  const st7 = sb.bandar_7d?.status || 'Netral';
  const s1 = st1.toLowerCase();
  const s7 = st7.toLowerCase();
  const acc1 = s1.includes('acc'), dist1 = s1.includes('dist');
  const acc7 = s7.includes('acc'), dist7 = s7.includes('dist');

  let badge = 'NETRAL / CAMPURAN', tone = 'slate', bStatus = 'Campuran', bText = `Bandar 1H: ${st1}, 7H: ${st7}.`, pbSt = 'warn';
  if (trendUp && acc1 && !dist7) {
    badge = 'KONFLUENSI KUAT'; tone = 'emerald'; bStatus = 'Akumulasi'; pbSt = 'ok';
    bText = `Harga naik didukung akumulasi broker (1H: ${st1}, 7H: ${st7}) — sinyal lebih sehat.`;
  } else if (trendUp && acc1 && dist7) {
    badge = 'AKUMULASI BARU (1H Acc)'; tone = 'sky'; bStatus = 'Mulai Akumulasi'; pbSt = 'warn';
    bText = `Hari ini broker mulai akumulasi (${st1}), meski 7 hari masih ${st7} — konfirmasi lanjutan besok.`;
  } else if (trendUp && dist1) {
    badge = 'WASPADA FAKEOUT (Divergensi)'; tone = 'amber'; bStatus = 'Distribusi'; pbSt = 'bad';
    bText = `Harga di atas MA20 tetapi hari ini broker distribusi (1H: ${st1}, 7H: ${st7}) — rawan tertahan.`;
  } else if (!trendUp && (acc1 || acc7)) {
    badge = 'AKUMULASI DIAM-DIAM'; tone = 'sky'; bStatus = 'Akumulasi'; pbSt = 'warn';
    bText = `Harga belum naik, namun broker menampung (1H: ${st1}, 7H: ${st7}) — pantau breakout.`;
  } else if (!trendUp && (dist1 || dist7)) {
    badge = 'TEKANAN JUAL KUAT'; tone = 'rose'; bStatus = 'Distribusi'; pbSt = 'bad';
    bText = `Tren turun dan broker distribusi (1H: ${st1}, 7H: ${st7}) — hindari menangkap pisau jatuh.`;
  }
  const c = _toneCls(tone);
  pill.textContent = badge;
  pill.className = `px-3 py-1.5 rounded-lg text-xs font-bold shrink-0 ${c.pill}`;
  p3.className = `rounded-lg border p-2.5 ${c.border}`;
  p3.innerHTML = `<div class="flex items-center justify-between gap-1"><span class="text-[10px] uppercase text-slate-400 font-semibold">4. Aliran Bandar</span><span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${c.pill}">${san(bStatus)}</span></div><div class="text-[11px] text-slate-200 mt-1 leading-snug">${san(bText)}</div>`;
  if (window._prebuyList?.[3]) { window._prebuyList[3].status = pbSt; window._prebuyList[3].detail = `${badge} — ${san(bText)}`; renderPrebuyChecklist(); }
}

async function runBacktest5y() {
  const sym = state.lastCek?.ticker || document.getElementById('cek-input').value.trim().toUpperCase();
  if (!sym) return;
  const body = document.getElementById('bt-body');
  const btn = document.getElementById('btn-bt-5y');
  if (btn) btn.disabled = true;
  body.innerHTML = `<div class="animate-pulse text-purple-300">Menjalankan backtest 5 tahun untuk ${esc(sym)}…</div>`;
  try {
    const d = await apiFetch(`/api/backtest/${encodeURIComponent(sym)}`);
    if (!d.ok) {
      body.innerHTML = `<div class="text-rose-400">${esc(d.error || 'Backtest tidak tersedia.')}</div>`;
      return;
    }
    body.innerHTML = `<div class="grid grid-cols-3 gap-2 mb-2">
        <div class="p-2 rounded bg-slate-900/80"><div class="text-[10px] text-slate-500">WIN RATE BELI</div><div class="mono font-bold ${(d.buy_win_rate_pct||0)>=50?'text-emerald-400':'text-amber-400'}">${d.buy_win_rate_pct != null ? d.buy_win_rate_pct + '%' : '—'}</div></div>
        <div class="p-2 rounded bg-slate-900/80"><div class="text-[10px] text-slate-500">AVG BELI (20H)</div><div class="mono font-bold ${(d.buy_avg_pct||0)>=0?'text-emerald-400':'text-rose-400'}">${fmtPct(d.buy_avg_pct)}</div></div>
        <div class="p-2 rounded bg-slate-900/80"><div class="text-[10px] text-slate-500">ACAK / BASELINE</div><div class="mono font-bold text-slate-200">${fmtPct(d.baseline_avg_pct)}</div></div>
      </div>
      <div class="text-[11px] text-slate-300 leading-snug">${d.plain_note || ''} <span class="text-slate-500">(${d.buy_trades || 0} sinyal BELI dari ${d.total_obs || 0} hari bursa).</span></div>`;
  } catch (e) {
    body.innerHTML = `<div class="text-rose-400">Gagal backtest: ${esc(e.message)}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

let _lastStockbit = null;
let _bandarWin = '1d';

async function loadStockbitLive(ticker) {
  const pill = document.getElementById('sb-status-pill');
  const body = document.getElementById('sb-body');
  if (!pill || !body) return;
  pill.textContent = 'Mengambil data…';
  pill.className = 'px-2 py-0.5 rounded-full text-[10px] font-semibold bg-sky-500/20 text-sky-300';
  body.innerHTML = `<div class="grid grid-cols-1 lg:grid-cols-2 gap-4 py-1"><div class="skeleton h-44 w-full"></div><div class="skeleton h-44 w-full"></div></div>`;
  try {
    const d = await apiFetch(`/api/stockbit/${encodeURIComponent(ticker)}`);
    _lastStockbit = d;
    if (!d.available) {
      pill.textContent = 'Nonaktif / Offline';
      pill.className = 'px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-800 text-slate-400';
      body.innerHTML = `<div class="py-2 text-slate-500">${esc(d.reason || 'Data Stockbit MCP tidak tersedia.')}</div>`;
      updateBandarConfluence(d);
      return;
    }
    pill.textContent = 'Live Read-Only';
    pill.className = 'px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/20 text-emerald-300';
    renderStockbitPanel();
    updateBandarConfluence(d);
  } catch (e) {
    pill.textContent = 'Gagal';
    pill.className = 'px-2 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/20 text-rose-300';
    body.innerHTML = `<div class="py-2 text-rose-400">Gagal memuat Stockbit MCP: ${esc(e.message)}</div>`;
  }
}

function switchBandarWin(win) {
  _bandarWin = win;
  document.querySelectorAll('.bw-btn').forEach(b => {
    const on = b.dataset.bw === win;
    b.className = `bw-btn px-2.5 py-1 rounded-md cursor-pointer ${on ? 'bg-sky-600 text-white font-semibold' : 'bg-slate-800 text-slate-300 hover:text-white'}`;
  });
  if (_lastStockbit?.available) renderStockbitPanel();
}

function renderStockbitPanel() {
  const body = document.getElementById('sb-body');
  const d = _lastStockbit;
  if (!body || !d?.available) return;
  const ob = d.orderbook || {};
  const bd = _bandarWin === '7d' ? (d.bandar_7d || d.bandar_1d) : (d.bandar_1d || d.bandar_7d);
  const bids = ob.bids || [];
  const offers = ob.offers || [];
  const maxLot = Math.max(1, ...bids.map(x => x.lots || 0), ...offers.map(x => x.lots || 0));
  const bidPct = ob.bid_pct != null ? Number(ob.bid_pct) : 50;
  const offPct = Math.max(0, 100 - bidPct);
  const fnet = ob.fnet;
  const fnetCls = (fnet || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400';

  const depthRows = Array.from({ length: Math.min(8, Math.max(bids.length, offers.length)) }, (_, i) => {
    const b = bids[i], o = offers[i];
    const bw = b ? Math.min(100, (b.lots / maxLot) * 100) : 0;
    const ow = o ? Math.min(100, (o.lots / maxLot) * 100) : 0;
    return `<div class="grid grid-cols-4 gap-1 items-center mono text-[11px] py-0.5 border-b border-slate-800/40">
      <div class="relative text-right pr-1.5 text-slate-300"><div class="absolute inset-y-0 right-0 bg-emerald-500/20 rounded-l" style="width:${bw}%"></div><span class="relative">${b ? fmtNum(b.lots) : ''}</span></div>
      <div class="text-right pr-2 font-bold text-emerald-400">${b ? fmtNum(b.price) : ''}</div>
      <div class="text-left pl-2 font-bold text-rose-400">${o ? fmtNum(o.price) : ''}</div>
      <div class="relative text-left pl-1.5 text-slate-300"><div class="absolute inset-y-0 left-0 bg-rose-500/20 rounded-r" style="width:${ow}%"></div><span class="relative">${o ? fmtNum(o.lots) : ''}</span></div>
    </div>`;
  }).join('');

  const bdStatus = bd?.status || 'Tidak tersedia';
  const bdCls = bdStatus.toLowerCase().includes('acc') ? 'bg-emerald-500/20 text-emerald-300' : (bdStatus.toLowerCase().includes('dist') ? 'bg-rose-500/20 text-rose-300' : 'bg-slate-800 text-slate-300');
  const fmtBrokerRow = (r, isBuy) => `<div class="flex items-center justify-between gap-2 py-1 border-b border-slate-800/50 text-[11px]">
    <div class="flex items-center gap-1.5 min-w-0">
      <span class="mono font-bold ${isBuy ? 'text-emerald-400' : 'text-rose-400'}">${r.code}</span>
      <span class="px-1 py-0.2 rounded bg-slate-800 text-[9px] text-slate-400">${(r.type || '').slice(0, 5)}</span>
      <span class="text-slate-300 truncate" title="${r.name || r.code}">${r.name || ''}</span>
    </div>
    <div class="shrink-0 text-right mono">
      <div class="${isBuy ? 'text-emerald-300' : 'text-rose-300'} font-semibold">${fmtCompactRp(Math.abs(r.value || 0))}</div>
      <div class="text-[10px] text-slate-500">@${r.avg ? fmtNum(r.avg) : '—'}</div>
    </div>
  </div>`;

  const ksList = d.keystats || [];
  const ksHtml = ksList.length
    ? `<div class="rounded-lg bg-slate-900/75 border border-slate-800/80 p-3 space-y-2 mt-3">
        <div class="flex items-center justify-between text-[11px]">
          <span class="font-semibold text-sky-300">Keystats Fundamental Resmi Stockbit (BEI)</span>
          <span class="text-[10px] text-slate-500">Pembanding &amp; pelengkap Yahoo Finance</span>
        </div>
        <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2">${ksList.map(k => `<div class="rounded bg-slate-950/70 border border-slate-800/80 px-2.5 py-1.5"><div class="text-[10px] text-slate-500">${k.label}</div><div class="mono text-xs font-bold text-slate-100 mt-0.5">${k.value}</div></div>`).join('')}</div>
      </div>`
    : '';

  body.innerHTML = `<div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <div class="rounded-lg bg-slate-900/75 border border-slate-800/80 p-3 space-y-2">
      <div class="flex items-center justify-between text-[11px]">
        <span class="font-semibold text-slate-200">Orderbook (Lot)</span>
        <span class="mono ${fnetCls}">Asing Net: ${fnet != null ? ((fnet >= 0 ? '+' : '-') + fmtCompactRp(Math.abs(fnet))) : '—'}</span>
      </div>
      <div class="space-y-1">
        <div class="flex justify-between text-[10px] mono"><span class="text-emerald-400">Bid ${bidPct.toFixed(1)}% (${ob.total_bid_lot || '—'})</span><span class="text-rose-400">Offer ${offPct.toFixed(1)}% (${ob.total_offer_lot || '—'})</span></div>
        <div class="h-1.5 w-full rounded-full bg-slate-800 overflow-hidden flex"><div class="bg-emerald-500 h-full" style="width:${bidPct}%"></div><div class="bg-rose-500 h-full" style="width:${offPct}%"></div></div>
      </div>
      <div class="grid grid-cols-4 gap-1 text-[10px] uppercase text-slate-500 border-b border-slate-800 pb-1">
        <div class="text-right pr-1.5">Bid Lot</div><div class="text-right pr-2">Bid</div><div class="text-left pl-2">Offer</div><div class="text-left pl-1.5">Offer Lot</div>
      </div>
      <div>${depthRows || '<div class="py-4 text-center text-slate-500">Orderbook tidak tersedia</div>'}</div>
    </div>

    <div class="rounded-lg bg-slate-900/75 border border-slate-800/80 p-3 space-y-2.5">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div>
          <span class="font-semibold text-slate-200 text-[11px]">Broker Summary (${bd?.from || '—'}${bd?.to && bd.to !== bd.from ? ' → ' + bd.to : ''})</span>
          <div class="text-[10px] text-slate-400">${bd ? `Buyer ${bd.buyers_count || 0} (Top3 ${bd.top3_buy_share}%) vs Seller ${bd.sellers_count || 0} (Top3 ${bd.top3_sell_share}%) · Avg ${bd.average ? fmtNum(bd.average) : '—'}` : ''}</div>
        </div>
        <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${bdCls}">${bdStatus}</span>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <div class="text-[10px] uppercase font-bold text-emerald-400 mb-1">Top 5 Akumulator</div>
          ${(bd?.top_buyers || []).map(r => fmtBrokerRow(r, true)).join('') || '<div class="text-slate-500 text-[11px]">—</div>'}
        </div>
        <div>
          <div class="text-[10px] uppercase font-bold text-rose-400 mb-1">Top 5 Distributor</div>
          ${(bd?.top_sellers || []).map(r => fmtBrokerRow(r, false)).join('') || '<div class="text-slate-500 text-[11px]">—</div>'}
        </div>
      </div>
    </div>
  </div>${ksHtml}`;
}

function toggleChartOpt(key) {
  state.chartOpts[key] = !state.chartOpts[key];
  document.querySelectorAll('.co-btn').forEach(b => {
    const on = state.chartOpts[b.dataset.co];
    b.style.opacity = on ? '1' : '0.35';
  });
  if (state.lastCek) renderTvChart(state.lastCek.candles || [], state.lastCek.plan || {}, state.lastCek.poc_60d);
}

function renderTvChart(candles, plan, poc60d) {
  const el = document.getElementById('tv-chart');
  el.innerHTML = '';
  if (!candles.length || !window.LightweightCharts) {
    el.innerHTML = '<div class="h-full flex items-center justify-center text-xs text-slate-500">Data grafik belum tersedia.</div>';
    return;
  }
  const chart = LightweightCharts.createChart(el, {
    width: el.clientWidth,
    height: 400,
    layout: { background: { color: '#060911' }, textColor: '#94a3b8' },
    grid: { vertLines: { color: '#111827' }, horzLines: { color: '#111827' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#1e293b', scaleMargins: { top: 0.08, bottom: state.chartOpts.vol ? 0.24 : 0.08 } },
    timeScale: { borderColor: '#1e293b' },
  });
  const cs = chart.addCandlestickSeries({
    upColor: '#22c55e', downColor: '#ef4444', borderVisible: false,
    wickUpColor: '#22c55e', wickDownColor: '#ef4444',
  });
  cs.setData(candles.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));

  if (state.chartOpts.vol) {
    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    volSeries.setData(candles.map(c => ({
      time: c.time,
      value: c.volume || 0,
      color: c.close >= c.open ? 'rgba(34, 197, 94, 0.30)' : 'rgba(239, 68, 68, 0.30)',
    })));
  }

  const s20 = candles.filter(c => c.sma20).map(c => ({ time: c.time, value: c.sma20 }));
  const s50 = candles.filter(c => c.sma50).map(c => ({ time: c.time, value: c.sma50 }));
  const s200 = candles.filter(c => c.sma200).map(c => ({ time: c.time, value: c.sma200 }));
  if (state.chartOpts.ma20 && s20.length) chart.addLineSeries({ color: '#38bdf8', lineWidth: 1.5, title: 'MA20' }).setData(s20);
  if (state.chartOpts.ma50 && s50.length) chart.addLineSeries({ color: '#f59e0b', lineWidth: 1.5, title: 'MA50' }).setData(s50);
  if (state.chartOpts.ma200 && s200.length) chart.addLineSeries({ color: '#a855f7', lineWidth: 1.5, title: 'MA200' }).setData(s200);

  if (state.chartOpts.levels) {
    if (plan?.entry) cs.createPriceLine({ price: plan.entry, color: '#38bdf8', lineWidth: 1, lineStyle: 2, title: 'ENTRY' });
    if (plan?.entry_pullback && plan.entry_pullback !== plan.entry) cs.createPriceLine({ price: plan.entry_pullback, color: '#06b6d4', lineWidth: 1, lineStyle: 3, title: 'PULLBACK' });
    if (plan?.sl) cs.createPriceLine({ price: plan.sl, color: '#ef4444', lineWidth: 1, lineStyle: 2, title: 'SL' });
    if (plan?.tp) cs.createPriceLine({ price: plan.tp, color: '#22c55e', lineWidth: 1, lineStyle: 2, title: 'TP' });
    if (poc60d) cs.createPriceLine({ price: poc60d, color: '#eab308', lineWidth: 1, lineStyle: 3, title: 'POC 60H' });
  }

  // Live Crosshair OHLCV + MA Legend
  const byTime = new Map(candles.map(c => [c.time, c]));
  const legendEl = document.getElementById('tv-legend');
  const showBar = (c) => {
    if (!c) return;
    const chgCls = c.close >= c.open ? 'text-emerald-400' : 'text-rose-400';
    legendEl.innerHTML = `<span class="text-slate-300">${esc(c.time)}</span> · O <b class="text-white">${fmtNum(c.open)}</b> · H <b class="text-emerald-300">${fmtNum(c.high)}</b> · L <b class="text-rose-300">${fmtNum(c.low)}</b> · C <b class="${chgCls}">${fmtNum(c.close)}</b> · Vol <b class="text-slate-200">${fmtNum(c.volume)}</b>${c.sma20 ? ` · <span class="text-sky-400">MA20 ${fmtNum(c.sma20)}</span>` : ''}${c.sma50 ? ` · <span class="text-amber-400">MA50 ${fmtNum(c.sma50)}</span>` : ''}${c.sma200 ? ` · <span class="text-purple-400">MA200 ${fmtNum(c.sma200)}</span>` : ''}${poc60d ? ` · <span class="text-yellow-400">POC ${fmtNum(poc60d)}</span>` : ''}`;
  };
  showBar(candles[candles.length - 1]);
  chart.subscribeCrosshairMove(param => {
    if (param?.time && byTime.has(param.time)) showBar(byTime.get(param.time));
    else showBar(candles[candles.length - 1]);
  });

  chart.timeScale().fitContent();
  new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth })).observe(el);
}

function idxTickSize(p) {
  if (p < 200) return 1;
  if (p < 500) return 2;
  if (p < 2000) return 5;
  if (p < 5000) return 10;
  return 25;
}

function roundIdxTick(p, mode = 'nearest') {
  const num = Number(p);
  if (!Number.isFinite(num) || num <= 1) return 1;
  const t = idxTickSize(num);
  const fn = mode === 'down' ? Math.floor : (mode === 'up' ? Math.ceil : Math.round);
  return Math.max(1, fn(num / t) * t);
}

function parseCalcNum(raw) {
  const s = String(raw ?? '').trim().replace(',', '.');
  const n = Number(s);
  return Number.isFinite(n) ? n : 0;
}

function copyTextSafe(txt) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(txt).catch(() => {
      const ta = document.createElement('textarea');
      ta.value = txt;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    });
  }
  const ta = document.createElement('textarea');
  ta.value = txt;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  document.execCommand('copy');
  ta.remove();
  return Promise.resolve();
}

function _calcRiskDist(entry) {
  const plan = state.lastCek?.plan || {};
  const snap = state.lastCek?.res?.snap || {};
  const atr = Number(plan.atr || snap.atr || 0);
  if (atr > 0) return atr;
  if (plan.entry && plan.sl && plan.entry > plan.sl) return plan.entry - plan.sl;
  return Math.max(idxTickSize(entry), entry * 0.04);
}

function prefillCalcFromCek(d) {
  const plan = d?.plan || {};
  const snap = d?.res?.snap || {};
  const entry = plan.entry ? roundIdxTick(plan.entry) : (snap.close ? roundIdxTick(snap.close) : 0);
  const dist = _calcRiskDist(entry || 100);
  let sl = plan.sl ? roundIdxTick(plan.sl) : 0;
  if (entry > 0 && (!sl || sl >= entry)) {
    sl = roundIdxTick(Math.max(1, entry - dist), 'down');
    if (sl >= entry) sl = Math.max(1, entry - idxTickSize(Math.max(1, entry - 1)));
  }
  let tp = plan.tp ? roundIdxTick(plan.tp) : 0;
  if (entry > 0 && (!tp || tp <= entry)) {
    tp = roundIdxTick(entry + 2 * Math.max(entry - sl, dist), 'up');
  }
  document.getElementById('calc-modal').value = Math.round(state.modal || state.defaultModal || 10000000);
  document.getElementById('calc-entry').value = entry || '';
  document.getElementById('calc-sl').value = sl || '';
  document.getElementById('calc-tp').value = tp || '';
  recalcSizing();
}

function resetCalcDefault() {
  if (!state.lastCek) return;
  document.getElementById('calc-riskpct').value = '1.5';
  prefillCalcFromCek(state.lastCek);
  showToast('Kalkulator direset ke level rencana default');
}

function autoFixCalcSl() {
  const entry = parseCalcNum(document.getElementById('calc-entry').value);
  if (entry <= 0) return;
  const dist = _calcRiskDist(entry);
  let newSl = roundIdxTick(Math.max(1, entry - dist), 'down');
  if (newSl >= entry) newSl = Math.max(1, entry - idxTickSize(Math.max(1, entry - 1)));
  document.getElementById('calc-sl').value = newSl;
  const curTp = parseCalcNum(document.getElementById('calc-tp').value);
  if (curTp <= entry) {
    document.getElementById('calc-tp').value = roundIdxTick(entry + 2 * (entry - newSl), 'up');
  }
  recalcSizing();
  showToast(`Stop-Loss disesuaikan otomatis ke Rp${fmtNum(newSl)}`);
}

function applyCalcRiskForOneLot(pct) {
  const clean = Math.max(0.25, Math.ceil(Number(pct) * 20) / 20);
  document.getElementById('calc-riskpct').value = clean.toFixed(2);
  recalcSizing();
  showToast(`Risiko Maks disesuaikan ke ${clean.toFixed(2)}% (1 lot)`);
}

function recalcSizing() {
  const modal = parseCalcNum(document.getElementById('calc-modal').value);
  const entry = parseCalcNum(document.getElementById('calc-entry').value);
  const sl = parseCalcNum(document.getElementById('calc-sl').value);
  const tp = parseCalcNum(document.getElementById('calc-tp').value);
  const riskPct = parseCalcNum(document.getElementById('calc-riskpct').value) || 1.5;
  const out = document.getElementById('calc-output');
  if (modal <= 0 || entry <= 0 || sl <= 0 || sl >= entry) {
    const fixBtn = (entry > 0 && sl >= entry)
      ? `<button onclick="autoFixCalcSl()" class="ml-2 px-2.5 py-1 rounded-md border border-amber-500/40 bg-amber-500/15 hover:bg-amber-500/25 text-[11px] font-semibold text-amber-300 cursor-pointer">Sesuaikan SL di bawah Entry</button>`
      : '';
    out.innerHTML = `<div class="col-span-full flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-200/90"><span>Masukkan Modal, Entry, dan SL (<b>SL harus di bawah Entry</b>) untuk menghitung lot &amp; risiko.</span>${fixBtn}</div>`;
    window._lastCalc = null;
    return;
  }
  const riskRpMax = modal * (riskPct / 100);
  const costPerLot = entry * 100 * 1.0015;
  const lossPerLotNet = (entry - sl) * 100 + (entry * 100 * 0.0015) + (sl * 100 * 0.0025);
  const profitPerLotNet = tp > entry ? ((tp - entry) * 100 - (entry * 100 * 0.0015) - (tp * 100 * 0.0025)) : 0;
  const maxLotsByRisk = Math.floor(riskRpMax / Math.max(1, lossPerLotNet));
  const maxLotsByCash = Math.floor(modal / Math.max(1, costPerLot));
  const lots = Math.max(0, Math.min(maxLotsByRisk, maxLotsByCash));
  const totalCost = lots * costPerLot;
  const totalLoss = lots * lossPerLotNet;
  const netProfit = lots * profitPerLotNet;
  const rr = tp > entry ? ((tp - entry) / (entry - sl)) : 0;
  window._lastCalc = { entry, sl, tp, lots, totalCost, totalLoss, netProfit, rr, costPerLot, lossPerLotNet };

  let noteHtml = '';
  if (lots === 0) {
    if (maxLotsByCash < 1) {
      noteHtml = `<div class="col-span-full rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-200">Harga 1 lot (${fmtRp(costPerLot)}) melebihi modal (${fmtRp(modal)}). Naikkan Modal minimal ke <b>${fmtRp(Math.ceil(costPerLot))}</b> untuk beli 1 lot.</div>`;
    } else {
      const needRiskPct = (lossPerLotNet / modal) * 100;
      noteHtml = `<div class="col-span-full flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-200"><span>Batas risiko ${riskPct}% (${fmtRp(riskRpMax)}) &lt; rugi 1 lot ke SL (${fmtRp(lossPerLotNet)} · ${needRiskPct.toFixed(2)}% modal).</span><button onclick="applyCalcRiskForOneLot(${needRiskPct.toFixed(3)})" class="px-2.5 py-1 rounded-md border border-amber-500/40 bg-amber-500/15 hover:bg-amber-500/25 font-semibold text-amber-300 cursor-pointer">Simulasi 1 Lot (${needRiskPct.toFixed(2)}%)</button></div>`;
    }
  }

  const refLot = lots > 0 ? lots : 1;
  const dispCost = lots > 0 ? totalCost : costPerLot;
  const dispLoss = lots > 0 ? totalLoss : lossPerLotNet;
  const dispProfit = lots > 0 ? netProfit : profitPerLotNet;
  const refSuffix = lots > 0 ? '' : ' (ref. 1 lot)';

  out.innerHTML = [
    ['Rekomendasi Lot', `${lots} lot`, lots > 0 ? `${lots * 100} lembar` : `0 lot sesuai batas risiko`, lots > 0 ? 'text-white' : 'text-amber-400'],
    ['Est. Modal Beli' + refSuffix, fmtRp(dispCost), `${((dispCost/modal)*100).toFixed(1)}% dari modal (${refLot} lot)`, 'text-sky-300'],
    ['Rugi Maks (SL)' + refSuffix, fmtRp(dispLoss), `-${((dispLoss/modal)*100).toFixed(2)}% modal`, 'text-rose-400'],
    ['Profit Net (TP)' + refSuffix, tp > entry ? fmtRp(dispProfit) : '—', tp > entry ? `+${((dispProfit/Math.max(1,dispCost))*100).toFixed(1)}%` : 'Isi TP > Entry', 'text-emerald-400'],
    ['Risk : Reward', rr > 0 ? `1 : ${rr.toFixed(2)}` : '—', rr >= 2 ? 'Ideal (≥ 1:2)' : 'Ketat (< 1:2)', rr >= 2 ? 'text-emerald-400' : 'text-amber-400'],
  ].map(([t,v,s,c]) => `<div class="rounded-lg bg-slate-900/80 border border-slate-800 px-3 py-2"><div class="text-[10px] uppercase text-slate-500">${t}</div><div class="mono text-xs font-bold mt-0.5 ${c}">${v}</div><div class="text-[10px] text-slate-400">${s}</div></div>`).join('') + noteHtml;
}

function applyCalcPullback() {
  const plan = state.lastCek?.plan || {};
  const pbRaw = plan.entry_pullback;
  if (!pbRaw) return showToast('Level pullback tidak tersedia untuk emiten ini');
  const pb = roundIdxTick(pbRaw);
  const dist = _calcRiskDist(pb);
  let newSl = roundIdxTick(Math.max(1, pb - dist), 'down');
  if (newSl >= pb) newSl = Math.max(1, pb - idxTickSize(Math.max(1, pb - 1)));
  document.getElementById('calc-entry').value = pb;
  document.getElementById('calc-sl').value = newSl;
  const curTp = parseCalcNum(document.getElementById('calc-tp').value);
  if (curTp <= pb) {
    document.getElementById('calc-tp').value = roundIdxTick(pb + 2 * (pb - newSl), 'up');
  }
  recalcSizing();
  showToast(`Entry diubah ke Pullback Rp${fmtNum(pb)} & SL disesuaikan ke Rp${fmtNum(newSl)}`);
}

function copyCalcOrder() {
  const c = window._lastCalc;
  const sym = state.lastCek?.ticker || document.getElementById('cek-input').value.trim().toUpperCase();
  if (!c) return showToast('Periksa Entry & SL terlebih dahulu (SL harus di bawah Entry)');
  const useLots = c.lots > 0 ? c.lots : 1;
  const cost = c.lots > 0 ? c.totalCost : c.costPerLot;
  const loss = c.lots > 0 ? c.totalLoss : c.lossPerLotNet;
  const note = c.lots > 0 ? '' : ' [CATATAN: melebihi batas risiko %, gunakan 1 lot saja bila sadar risiko]';
  const txt = `ORDER MANUAL STOCKBIT — ${sym}\nBUY LIMIT: Rp${fmtNum(c.entry)} × ${useLots} lot (${fmtRp(cost)})\nSTOP-LOSS: Rp${fmtNum(c.sl)} (risiko ${fmtRp(loss)})\nTARGET TP: Rp${fmtNum(c.tp)} (R:R 1:${c.rr.toFixed(2)})${note}`;
  copyTextSafe(txt);
  showToast(c.lots > 0 ? 'Tiket order simulasi disalin ke clipboard' : 'Tiket referensi 1 lot disalin (perhatikan catatan risiko)');
}

function prefillCalcBuy() {
  const c = window._lastCalc;
  const sym = state.lastCek?.ticker || document.getElementById('cek-input').value.trim().toUpperCase();
  prefillExec(sym, c?.entry || state.lastCek?.plan?.entry || 0, Math.max(1, c?.lots || 1));
}

// TAB 8: ALERT & RADAR
const ALERT_LABELS = {
  jump: 'Lonjakan +%', drop: 'Anjlok -%', breakout: 'Breakout 20H', breakdown: 'Breakdown 20H',
  oversold: 'RSI Oversold', overbought: 'RSI Overbought', ma_golden: 'Golden Cross', ma_death: 'Death Cross', vol_spike: 'Lonjakan Volume',
};

function renderAlertChips() {
  document.getElementById('alert-kind-chips').innerHTML = state.alertKinds.map(k => {
    const on = state.selectedAlertKinds.has(k);
    return `<button onclick="toggleAlertKind('${k}')" class="px-2.5 py-1 rounded-lg text-xs border cursor-pointer transition ${on ? 'bg-sky-600/25 border-sky-500 text-sky-200 font-semibold' : 'bg-slate-900 border-slate-700 text-slate-400 hover:text-white'}">${esc(ALERT_LABELS[k] || k)}</button>`;
  }).join('');
}

function toggleAlertKind(k) {
  if (state.selectedAlertKinds.has(k)) state.selectedAlertKinds.delete(k);
  else state.selectedAlertKinds.add(k);
  renderAlertChips();
}

async function loadAlerts() {
  renderAlertChips();
  setRefreshing(true);
  try {
    const d = await apiFetch(`/api/alerts?chat_id=${encodeURIComponent(state.chatId)}`);
    if (d.available_kinds?.length) state.alertKinds = d.available_kinds;
    renderAlertChips();
    const items = d.items || [];
    document.getElementById('badge-alerts').textContent = items.length;
    const tb = document.getElementById('alerts-tbody');
    if (!items.length) {
      tb.innerHTML = `<tr><td colspan="3" class="px-4 py-10 text-center text-slate-500">Belum ada alert aktif. Masukkan kode saham &amp; pilih kondisi di atas.</td></tr>`;
      return;
    }
    tb.innerHTML = items.map(it => `<tr onclick="openCek('${esc(it.ticker)}')" class="hover:bg-slate-800/50 cursor-pointer">
      <td class="px-4 py-3 mono font-bold text-white">${esc(it.ticker)}</td>
      <td class="px-3 py-3"><div class="flex flex-wrap gap-1">${(it.kinds || []).map(k => `<span class="px-2 py-0.5 rounded-md bg-sky-500/15 text-sky-300 text-[11px]">${esc(ALERT_LABELS[k] || k)}</span>`).join('')}</div></td>
      <td class="px-3 py-3 text-right" onclick="event.stopPropagation()">
        <button onclick="removeAlert('${esc(it.ticker)}')" class="text-rose-400 hover:text-rose-300 text-xs cursor-pointer">Hapus</button>
      </td>
    </tr>`).join('');
  } finally { setRefreshing(false); }
}

async function saveAlert() {
  const ticker = document.getElementById('alert-ticker').value.trim().toUpperCase();
  const kinds = [...state.selectedAlertKinds];
  if (!ticker) return showToast('Masukkan kode saham (mis. BBCA)');
  if (!kinds.length) return showToast('Pilih minimal 1 kondisi alert');
  try {
    await apiFetch('/api/alerts', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ chat_id: state.chatId, ticker, kinds }),
    });
    document.getElementById('alert-ticker').value = '';
    showToast(`Alert ${ticker} disimpan (${kinds.length} kondisi)`);
    loadAlerts();
  } catch (e) { showToast(e.message); }
}

async function removeAlert(ticker) {
  await apiFetch(`/api/alerts/${encodeURIComponent(ticker)}?chat_id=${encodeURIComponent(state.chatId)}`, { method: 'DELETE' });
  showToast(`Alert ${ticker} dihapus`);
  loadAlerts();
}

function enableDesktopNotifications() {
  if (!('Notification' in window)) return showToast('Browser ini tidak mendukung Notifikasi Desktop');
  Notification.requestPermission().then(perm => {
    const btn = document.getElementById('btn-notif-perm');
    if (perm === 'granted') {
      if (btn) btn.textContent = 'Notifikasi: Aktif';
      showToast('Notifikasi Desktop diaktifkan');
      new Notification('SahamBot BEI', { body: 'Notifikasi alert & SL/TP portofolio aktif.' });
    } else {
      showToast('Izin notifikasi ditolak browser');
    }
  });
}

async function checkAlertsNow(silent = false) {
  const btn = document.getElementById('btn-check-alerts');
  if (!silent) setRefreshing(true);
  if (btn) { btn.disabled = true; btn.textContent = 'Memindai…'; }
  try {
    const d = await apiFetch(`/api/alerts/check?chat_id=${encodeURIComponent(state.chatId)}`, { method: 'POST' });
    const card = document.getElementById('alerts-triggered-card');
    const list = document.getElementById('alerts-triggered-list');
    const ts = document.getElementById('alerts-checked-at');
    const hits = d.triggered || [];
    if (ts) ts.textContent = `Dicek ${d.checked_at || ''} (${d.positions_checked || 0} posisi · ${d.alerts_checked || 0} alert)`;
    if (card && list) {
      card.classList.remove('hidden');
      if (!hits.length) {
        list.innerHTML = '<div class="text-slate-400 py-1">Tidak ada pemicu alert atau SL/TP portofolio yang tersentuh saat ini.</div>';
      } else {
        list.innerHTML = hits.map(h => {
          const cls = h.severity === 'danger' ? 'border-rose-500/40 bg-rose-500/10 text-rose-200' : (h.severity === 'ok' ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200' : 'border-sky-500/40 bg-sky-500/10 text-sky-200');
          return `<div onclick="openCek('${h.ticker}')" class="p-2.5 rounded-lg border ${cls} cursor-pointer flex items-center justify-between gap-2">
            <div><div class="font-bold">${h.title}</div><div class="text-[11px] opacity-90">${h.detail}</div></div>
            <span class="mono text-[11px] font-bold shrink-0">Buka →</span>
          </div>`;
        }).join('');
      }
    }
    if (hits.length) {
      showToast(`${hits.length} pemicu alert/SL/TP terdeteksi!`);
      if ('Notification' in window && Notification.permission === 'granted') {
        hits.slice(0, 2).forEach(h => new Notification(h.title, { body: h.detail }));
      }
    } else if (!silent) {
      showToast('Pindaian selesai — belum ada alert yang terpicu');
    }
  } catch (e) {
    if (!silent) showToast('Gagal cek alert: ' + e.message);
  } finally {
    if (!silent) setRefreshing(false);
    if (btn) { btn.disabled = false; btn.textContent = 'Cek Alert & SL/TP Sekarang'; }
  }
}

// AUTO-REFRESH & CSV EXPORT
function applyAutoRefreshTimer(val, silent = false) {
  clearInterval(window._autoRefTimer);
  const opts = state.refreshOptions || { Manual: 0, '5m': 5, '15m': 15, '30m': 30 };
  let mins = 0;
  if (Object.prototype.hasOwnProperty.call(opts, val)) {
    mins = Number(opts[val]) || 0;
  } else {
    const sec = Number(val) || 0;
    mins = sec > 0 ? sec / 60 : 0;
  }
  if (mins > 0) {
    window._autoRefTimer = setInterval(() => {
      if (state.tab !== 'compare') refreshCurrent();
    }, mins * 60 * 1000);
    if (!silent) showToast(`Auto-refresh aktif setiap ${mins} menit`);
  } else if (!silent) {
    showToast('Auto-refresh dimatikan (Manual)');
  }
}

async function setAutoRefresh(val) {
  state.refresh = val;
  syncRefreshSelectors();
  applyAutoRefreshTimer(val, false);
  try {
    await apiFetch('/api/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: state.chatId, modal: state.modal, syariah: state.syariah, refresh: state.refresh }),
    });
  } catch (_) {}
}

function downloadCsv(filename, headers, rows) {
  const esc = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const csv = [headers.map(esc).join(','), ...rows.map(r => r.map(esc).join(','))].join('\r\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
  showToast(`Diekspor ke ${filename}`);
}

function exportSinyalCsv() {
  const rows = state.sinyalRows || [];
  if (!rows.length) return showToast('Tidak ada data sinyal untuk diekspor');
  downloadCsv(
    `sahambot_sinyal_${new Date().toISOString().slice(0, 10)}.csv`,
    ['Ticker', 'Nama', 'Sektor', 'Harga', 'Ret1H%', 'Sinyal', 'Rank%', 'RSI14', 'Alasan'],
    rows.map(r => [r.ticker, r.name, r.sector, r.price, r.ret1d, r.signal, r.rel_pct, r.rsi, r.reason])
  );
}

function exportPortoCsv() {
  const rows = state.portoRows || [];
  if (!rows.length) return showToast('Portofolio kosong');
  downloadCsv(
    `sahambot_portofolio_${new Date().toISOString().slice(0, 10)}.csv`,
    ['Ticker', 'Lot', 'AvgBeli', 'Harga', 'NilaiPasar', 'PnLRp', 'PnLNet%', 'SLEfektif', 'Tesis', 'Alasan'],
    rows.map(r => [r.ticker, r.lots, r.avg_price, r.price, r.value_rp, r.pnl_rp, r.pnl_net_pct, r.effective_sl, r.thesis, (r.reasons || []).join('; ')])
  );
}

// PELACAK AKURASI H+5 / H+20
async function recordCekAccuracy() {
  const d = state.lastCek;
  if (!d) return showToast('Buka analisis emiten terlebih dahulu');
  const plan = d.plan || {};
  const snap = (d.res || {}).snap || {};
  const entryHi = plan.entry || snap.close;
  const entryLo = plan.entry_pullback || entryHi;
  if (!entryHi || !plan.sl || !plan.tp) return showToast('Level Entry/SL/TP belum tersedia untuk dicatat');
  try {
    await apiFetch('/api/accuracy/record', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker: d.ticker,
        verdict: (d.cek || {}).verdict || 'TUNGGU',
        entry_low: Math.min(entryLo, entryHi),
        entry_high: Math.max(entryLo, entryHi),
        sl: plan.sl,
        tp1: plan.tp,
        power_score: (d.powerscore || {}).pct,
        notes: 'Dicatat dari Desktop Modern',
      }),
    });
    showToast(`${d.ticker} dicatat ke Pelacak Akurasi H+5/H+20`);
  } catch (e) { showToast('Gagal mencatat: ' + e.message); }
}

function renderAccuracy(d) {
  const s = d.summary || {};
  document.getElementById('acc-kpis').innerHTML = [
    ['Total Tercatat', `${s.total_records || 0}`, `Pending ${s.pending || 0} · Open ${s.open || 0}`, 'text-white'],
    ['Fill Rate (H+5)', s.fill_rate_pct != null ? `${s.fill_rate_pct}%` : '—', `Terisi ${s.filled_count || 0} · Expired ${s.expired_unfilled || 0}`, 'text-sky-400'],
    ['Win Rate (TP1)', s.win_rate_pct != null ? `${s.win_rate_pct}%` : '—', `TP1 ${s.tp1_hit_count || 0} · SL ${s.sl_hit_count || 0}`, 'text-emerald-400'],
    ['Avg Return Closed', s.avg_closed_return_pct != null ? fmtPct(s.avg_closed_return_pct) : '—', `${s.closed_count || 0} selesai`, (s.avg_closed_return_pct || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'],
  ].map(([t, v, sub, cls]) => `<div class="rounded-lg bg-slate-900/75 border border-slate-800 p-3"><div class="text-[10px] uppercase text-slate-400">${t}</div><div class="mono text-base font-bold mt-0.5 ${cls}">${v}</div><div class="text-[10px] text-slate-500">${sub}</div></div>`).join('');

  const recs = d.records || [];
  const tb = document.getElementById('acc-tbody');
  if (!recs.length) {
    tb.innerHTML = `<tr><td colspan="7" class="px-3 py-6 text-center text-slate-500">Belum ada rekomendasi tercatat. Klik <b>+ Lacak Akurasi</b> di tab Cek Emiten untuk mulai mengaudit akurasi sinyal.</td></tr>`;
    return;
  }
  tb.innerHTML = recs.map(r => `<tr onclick="openCek('${esc(r.ticker)}')" class="hover:bg-slate-800/50 cursor-pointer">
    <td class="px-3 py-2 mono text-slate-400">${esc(r.rec_date || '—')}</td>
    <td class="px-3 py-2 mono font-bold text-white">${esc(r.ticker)}</td>
    <td class="px-3 py-2 text-center">${esc(r.verdict)}</td>
    <td class="px-3 py-2 text-right mono text-sky-300">${fmtNum(r.entry_low)}–${fmtNum(r.entry_high)}</td>
    <td class="px-3 py-2 text-right mono"><span class="text-rose-400">${fmtNum(r.sl)}</span> / <span class="text-emerald-400">${fmtNum(r.tp1)}</span></td>
    <td class="px-3 py-2 text-center"><span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-300">${esc(r.outcome || r.status)}</span></td>
    <td class="px-3 py-2 text-right mono ${(r.return_pct || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${r.return_pct != null ? fmtPct(r.return_pct) : '—'}</td>
  </tr>`).join('');
}

async function loadAccuracy() {
  try { renderAccuracy(await apiFetch('/api/accuracy')); } catch (_) {}
}

async function evaluateAccuracy() {
  setRefreshing(true);
  try {
    const d = await apiFetch('/api/accuracy/evaluate', { method: 'POST' });
    renderAccuracy(d);
    showToast('Evaluasi akurasi H+5 / H+20 selesai diperbarui');
  } catch (e) { showToast('Gagal evaluasi: ' + e.message); }
  finally { setRefreshing(false); }
}

// TAB 9: BANDINGKAN MULTI-EMITEN
function presetCompare(list) {
  document.getElementById('compare-input').value = list;
  loadCompare();
}

async function loadCompare() {
  const raw = document.getElementById('compare-input').value.trim();
  if (!raw) return showToast('Masukkan 2–4 kode saham dipisahkan koma');
  setRefreshing(true);
  window._compareLoaded = true;
  const wrap = document.getElementById('compare-grid');
  wrap.innerHTML = '<div class="py-10 text-center text-slate-400 animate-pulse">Menganalisis &amp; membandingkan emiten secara paralel…</div>';
  try {
    const d = await apiFetch(`/api/compare?tickers=${encodeURIComponent(raw)}&chat_id=${encodeURIComponent(state.chatId)}&modal=${state.modal}`);
    const items = (d.items || []).filter(x => x.ok);
    if (!items.length) {
      wrap.innerHTML = '<div class="py-8 text-center text-rose-400">Gagal mengambil data untuk kode saham tersebut.</div>';
      return;
    }
    const maxOf = key => Math.max(...items.map(it => it[key] ?? -Infinity));
    const bestPs = maxOf('powerscore_pct');
    const bestFund = maxOf('fund_pct');
    const bestRet20 = maxOf('ret20d');
    const bestRr = maxOf('rr');

    const rows = [
      ['Harga Terakhir', it => `<b class="mono text-white">${fmtRp(it.close)}</b>`],
      ['Return 1H / 20H', it => `<span class="mono ${(it.ret1d||0)>=0?'text-emerald-400':'text-rose-400'}">${fmtPct(it.ret1d)}</span> · <span class="mono ${it.ret20d===bestRet20?'text-emerald-300 font-bold':'text-slate-300'}">${fmtPct(it.ret20d)}</span>`],
      ['Verdict Ceklist', it => `<span class="px-2 py-0.5 rounded-full text-[11px] font-bold ${it.verdict==='LAYAK'?'bg-emerald-500/20 text-emerald-300':(it.verdict==='HINDARI'?'bg-rose-500/20 text-rose-300':'bg-amber-500/20 text-amber-300')}">${it.verdict} (${it.points ?? '-'} pt)</span>`],
      ['PowerScore', it => `<span class="mono ${it.powerscore_pct===bestPs?'text-emerald-400 font-bold':'text-slate-200'}">${it.powerscore_rating || '—'} (${it.powerscore_pct ?? '—'}%)</span>`],
      ['Skor Fundamental', it => `<span class="mono ${it.fund_pct===bestFund?'text-emerald-400 font-bold':'text-slate-200'}">${it.fund_pct != null ? Math.round(it.fund_pct) + '%' : '—'}</span>`],
      ['FCF Yield / EV-EBITDA', it => `<span class="mono text-slate-300">${it.fcf_yield != null ? Number(it.fcf_yield).toFixed(1) + '%' : '—'} · ${it.ev_ebitda != null ? Number(it.ev_ebitda).toFixed(1) + 'x' : '—'}</span>`],
      ['RSI (14)', it => `<span class="mono text-sky-300">${it.rsi != null ? Number(it.rsi).toFixed(1) : '—'}</span>`],
      ['Entry / SL / TP', it => `<span class="mono"><span class="text-sky-300">${fmtNum(it.entry)}</span> / <span class="text-rose-400">${fmtNum(it.sl)}</span> / <span class="text-emerald-400">${fmtNum(it.tp)}</span></span>`],
      ['Risk : Reward', it => `<span class="mono ${it.rr===bestRr?'text-emerald-400 font-bold':'text-slate-300'}">${it.rr ? '1 : ' + Number(it.rr).toFixed(2) : '—'}</span>`],
      ['Bandar 1H / 7H (MCP)', it => it.bandar_1d ? `<span class="text-slate-200">${it.bandar_1d} / ${it.bandar_7d || '—'}</span>` : '<span class="text-slate-500">MCP Off</span>'],
      ['Asing Net (MCP)', it => it.fnet != null ? `<span class="mono ${it.fnet>=0?'text-emerald-400':'text-rose-400'}">${it.fnet>=0?'+':'-'}${fmtCompactRp(Math.abs(it.fnet))}</span>` : '<span class="text-slate-500">—</span>'],
      ['Syariah (ISSI)', it => it.syariah ? '<span class="text-emerald-400 font-semibold">Ya (ISSI)</span>' : '<span class="text-slate-500">Non-ISSI</span>'],
    ];

    wrap.innerHTML = `<table class="w-full text-left text-xs">
      <thead class="border-b border-slate-800">
        <tr>
          <th class="px-3 py-3 text-slate-400 uppercase text-[11px] w-44">Metrik</th>
          ${items.map(it => `<th class="px-3 py-3"><div class="flex items-center justify-between gap-2"><div><button onclick="openCek('${esc(it.ticker)}')" class="mono text-sm font-bold text-sky-300 hover:underline cursor-pointer">${esc(it.ticker)}</button><div class="text-[10px] text-slate-400 font-normal truncate max-w-[160px]">${it.name}</div></div></div></th>`).join('')}
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-800/60">
        ${rows.map(([label, fn]) => `<tr class="hover:bg-slate-800/30"><td class="px-3 py-2.5 text-slate-400 font-medium">${label}</td>${items.map(it => `<td class="px-3 py-2.5">${fn(it)}</td>`).join('')}</tr>`).join('')}
      </tbody>
    </table>`;
  } catch (e) {
    wrap.innerHTML = `<div class="py-8 text-center text-rose-400">Gagal membandingkan: ${esc(e.message)}</div>`;
  } finally { setRefreshing(false); }
}

// TAB 10: BACKTEST SIMULATOR (EQUITY CURVE + TRADE LEDGER)
async function runFullBacktest() {
  const sym = (document.getElementById('bt-ticker')?.value || '').trim().toUpperCase();
  if (!sym) return showToast('Masukkan kode saham untuk backtest');
  const strategy = document.getElementById('bt-strategy')?.value || 'signal';
  const period = document.getElementById('bt-period')?.value || '3y';
  const modal = Number(document.getElementById('bt-modal')?.value) || 10000000;
  const sl = Number(document.getElementById('bt-sl')?.value) || 5;
  const tp = Number(document.getElementById('bt-tp')?.value) || 10;
  const hold = Number(document.getElementById('bt-hold')?.value) || 20;

  window._btLoaded = true;
  setRefreshing(true);
  const btn = document.getElementById('btn-run-bt-sim');
  const spin = document.getElementById('bt-sim-spin');
  const lbl = document.getElementById('bt-sim-lbl');
  if (btn) btn.disabled = true;
  if (spin) spin.classList.remove('hidden');
  if (lbl) lbl.textContent = `Menghitung ${esc(sym)}…`;
  document.getElementById('bt-eq-chart').innerHTML = '<div class="skeleton h-full w-full"></div>';

  try {
    const url = `/api/backtest/${encodeURIComponent(sym)}/simulate?period=${period}&modal=${modal}&sl_pct=${sl}&tp_pct=${tp}&max_hold=${hold}&strategy=${strategy}`;
    const d = await apiFetch(url);
    if (!d.ok) {
      document.getElementById('bt-eq-chart').innerHTML = `<div class="h-full flex items-center justify-center text-xs text-rose-400">${esc(d.error || 'Backtest gagal.')}</div>`;
      return;
    }
    const m = d.metrics || {};
    const noteEl = document.getElementById('bt-sim-note');
    noteEl.classList.remove('hidden');
    noteEl.innerHTML = `<b class="text-sky-300">Ringkasan Backtest ${d.ticker} (${d.period}):</b> ${san(m.plain_note || '')}`;

    document.getElementById('bt-sim-kpis').innerHTML = [
      ['Modal Akhir Strategi', fmtRp(m.final_capital), fmtPct(m.strategy_ret_pct), (m.strategy_ret_pct || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'],
      ['Buy & Hold', fmtRp(m.buyhold_final), fmtPct(m.buyhold_ret_pct), (m.buyhold_ret_pct || 0) >= 0 ? 'text-slate-200' : 'text-rose-300'],
      ['Alpha vs B&H', fmtPct(m.alpha_pct), (m.alpha_pct || 0) >= 0 ? 'Unggul dari B&H' : 'Di bawah B&H', (m.alpha_pct || 0) >= 0 ? 'text-emerald-400' : 'text-amber-400'],
      ['Win Rate', m.win_rate_pct != null ? `${m.win_rate_pct}%` : '—', `${m.trades_count || 0} trade · avg ${m.avg_hold_days || 0}h`, (m.win_rate_pct || 0) >= 50 ? 'text-emerald-400' : 'text-amber-400'],
      ['Max Drawdown', fmtPct(m.max_drawdown_pct), 'Penurunan puncak→lembah', 'text-rose-400'],
      ['Profit Factor', m.profit_factor != null ? `${m.profit_factor}x` : '—', `Win ${fmtPct(m.avg_win_pct)} / Loss ${fmtPct(m.avg_loss_pct)}`, (m.profit_factor || 0) >= 1.2 ? 'text-emerald-400' : 'text-slate-300'],
    ].map(([t, v, s, cls]) => `<div class="glass-card rounded-xl p-3"><div class="text-[10px] uppercase text-slate-400 font-semibold">${t}</div><div class="mono text-sm font-bold mt-1 ${cls}">${v}</div><div class="text-[10px] text-slate-400 mt-0.5 truncate">${s}</div></div>`).join('');

    renderBacktestChart(d.equity_curve || []);

    const trades = d.trades || [];
    document.getElementById('bt-trades-count').textContent = `${m.trades_count || trades.length} transaksi`;
    const tb = document.getElementById('bt-trades-tbody');
    if (!trades.length) {
      tb.innerHTML = `<tr><td colspan="9" class="px-4 py-8 text-center text-slate-500">Tidak ada transaksi simulasi pada parameter ini.</td></tr>`;
    } else {
      tb.innerHTML = trades.map(tr => {
        const badge = tr.exit_reason === 'TP' ? 'bg-emerald-500/20 text-emerald-300' : (tr.exit_reason === 'SL' ? 'bg-rose-500/20 text-rose-300' : 'bg-slate-800 text-slate-300');
        const pCls = (tr.net_pnl_rp || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400';
        return `<tr class="hover:bg-slate-800/40">
          <td class="px-4 py-2 mono text-slate-300">${tr.entry_date}</td>
          <td class="px-3 py-2 mono text-slate-300">${tr.exit_date}</td>
          <td class="px-3 py-2 text-right mono">${tr.lots}</td>
          <td class="px-3 py-2 text-right mono">${fmtNum(tr.entry_price)}</td>
          <td class="px-3 py-2 text-right mono">${fmtNum(tr.exit_price)}</td>
          <td class="px-3 py-2 text-right mono text-slate-400">${tr.hold_days}h</td>
          <td class="px-3 py-2 text-center"><span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${badge}">${tr.exit_reason}</span></td>
          <td class="px-3 py-2 text-right mono font-semibold ${pCls}">${fmtRp(tr.net_pnl_rp)}</td>
          <td class="px-3 py-2 text-right mono font-semibold ${pCls}">${fmtPct(tr.net_pnl_pct)}</td>
        </tr>`;
      }).join('');
    }
    setStatus(`Backtest ${d.ticker} selesai (${m.trades_count || 0} trade)`);
  } catch (e) {
    showToast('Gagal backtest: ' + e.message);
  } finally {
    setRefreshing(false);
    if (btn) btn.disabled = false;
    if (spin) spin.classList.add('hidden');
    if (lbl) lbl.textContent = 'Jalankan Backtest';
  }
}

function renderBacktestChart(curve) {
  const el = document.getElementById('bt-eq-chart');
  el.innerHTML = '';
  if (!curve.length || !window.LightweightCharts) {
    el.innerHTML = '<div class="h-full flex items-center justify-center text-xs text-slate-500">Data kurva ekuitas tidak tersedia.</div>';
    return;
  }
  const chart = LightweightCharts.createChart(el, {
    width: el.clientWidth,
    height: 340,
    layout: { background: { color: '#060911' }, textColor: '#94a3b8' },
    grid: { vertLines: { color: '#111827' }, horzLines: { color: '#111827' } },
    rightPriceScale: { borderColor: '#1e293b' },
    timeScale: { borderColor: '#1e293b' },
  });
  const sStrat = chart.addAreaSeries({
    lineColor: '#10b981', topColor: 'rgba(16, 185, 129, 0.28)', bottomColor: 'rgba(16, 185, 129, 0.01)', lineWidth: 2,
  });
  const sBh = chart.addLineSeries({ color: '#94a3b8', lineWidth: 1.5, lineStyle: 2 });
  sStrat.setData(curve.map(c => ({ time: c.time, value: c.strategy })));
  sBh.setData(curve.map(c => ({ time: c.time, value: c.buyhold })));

  const byTime = new Map(curve.map(c => [c.time, c]));
  const leg = document.getElementById('bt-eq-legend');
  const showPt = p => {
    if (!p || !leg) return;
    leg.innerHTML = `<span class="text-slate-300">${esc(p.time)}</span> · Strategi: <b class="text-emerald-400">${fmtRp(p.strategy)}</b> · Buy &amp; Hold: <b class="text-slate-300">${fmtRp(p.buyhold)}</b>`;
  };
  showPt(curve[curve.length - 1]);
  chart.subscribeCrosshairMove(param => {
    if (param?.time && byTime.has(param.time)) showPt(byTime.get(param.time));
    else showPt(curve[curve.length - 1]);
  });
  chart.timeScale().fitContent();
  new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth })).observe(el);
}

async function cekAddWatch() {
  const t = document.getElementById('cek-input').value.trim().toUpperCase();
  if (!t) return;
  await apiFetch('/api/watchlist', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ chat_id: state.chatId, ticker: t }) });
  showToast(`${t} ditambahkan ke Watchlist`);
}
function cekCopyOrder() {
  const txt = state.lastCek?.order_ticket;
  if (!txt) return showToast('Rencana beli belum valid untuk disalin');
  navigator.clipboard.writeText(txt);
  showToast('Tiket order disalin ke clipboard');
}
function cekPrefillBuy() {
  if (!state.lastCek) return;
  const p = state.lastCek.plan || {};
  const snap = (state.lastCek.res || {}).snap || {};
  prefillExec(state.lastCek.ticker, p.entry || snap.close, p.lots || 1);
}

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    openCmdPalette();
  } else if (e.key === 'Escape') {
    closeCmdPalette();
    closeProfileModal();
  } else if (e.key === 'F5' && !e.ctrlKey && !e.shiftKey && !e.altKey) {
    e.preventDefault();
    refreshCurrent();
  }
});
document.getElementById('cmd-input')?.addEventListener('input', (e) => {
  const q = e.target.value.trim();
  const cmdWrap = document.getElementById('cmd-tickers');
  if (!cmdWrap) return;
  if (!q) { renderRecentTickers(); return; }
  const hits = _matchUniverse(q, 10);
  cmdWrap.innerHTML = hits.length
    ? hits.map(s => `<button onclick="closeCmdPalette();openCek('${esc(s.code)}')" class="px-2.5 py-1 rounded-md bg-slate-900 hover:bg-sky-600/30 border border-slate-700 text-xs text-slate-200 cursor-pointer transition"><b class="mono text-sky-300">${esc(s.code)}</b> <span class="text-slate-400 text-[10px]">${esc((s.name||'').slice(0,22))}</span></button>`).join('')
    : `<span class="text-xs text-slate-500">Tekan Enter untuk langsung cek "${esc(q.toUpperCase())}"</span>`;
});
document.getElementById('cmd-input')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.value.trim()) {
    const raw = e.target.value.trim();
    const hits = _matchUniverse(raw, 1);
    const sym = (raw.length > 4 && hits.length) ? hits[0].code : raw.toUpperCase();
    closeCmdPalette();
    openCek(sym);
  }
});
document.getElementById('global-search').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.value.trim()) {
    openCek(e.target.value.trim().toUpperCase());
    e.target.value = '';
  }
});
document.getElementById('cek-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    const raw = e.target.value.trim();
    const hits = _matchUniverse(raw, 1);
    if (raw && raw.length > 4 && hits.length) e.target.value = hits[0].code;
    closeCekDropdown();
    loadCek();
  } else if (e.key === 'Escape') {
    closeCekDropdown();
  }
});
document.getElementById('watch-add-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') addWatchlist(); });

// Boot
async function bootAppData() {
  renderRecentTickers();
  loadUniverse();
  setSinyalFilter('ALL');
  await loadProfile();
  switchTab('pasar');
  // Preload badge Portofolio & Alert di latar belakang
  apiFetch(`/api/portfolio?chat_id=${encodeURIComponent(state.chatId)}`)
    .then(d => {
      state.portoRows = d.rows || [];
      document.getElementById('badge-porto').textContent = state.portoRows.length;
    })
    .catch(() => {});
  apiFetch(`/api/alerts?chat_id=${encodeURIComponent(state.chatId)}`)
    .then(d => { document.getElementById('badge-alerts').textContent = d.total || 0; })
    .catch(() => {});
  apiFetch('/api/health')
    .then(d => {
      const dot = document.getElementById('mcp-dot');
      const txt = document.getElementById('mcp-txt');
      if (dot && txt) {
        dot.className = `w-2 h-2 rounded-full ${d.stockbit_mcp ? 'bg-emerald-400 pulse-dot' : 'bg-slate-600'}`;
        txt.textContent = d.stockbit_mcp ? 'Stockbit MCP: Live' : 'Stockbit MCP: Off';
      }
    })
    .catch(() => {});
}

document.getElementById('pin-unlock-input')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') unlockMobilePin();
});

window.addEventListener('DOMContentLoaded', async () => {
  if (window.lucide) lucide.createIcons();
  try {
    const mi = await apiFetch('/api/mobile-info');
    if (mi.auth_required) {
      document.getElementById('pin-lock-overlay').classList.remove('hidden');
      document.getElementById('pin-unlock-input')?.focus();
      return;
    }
  } catch (_) {}
  await bootAppData();
});
