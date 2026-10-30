// 3EDIČ — entry point: router, shell, home, first-run setup and settings.
import { t, setLang, getLang, pad3, fmtTime } from './i18n.js';
import { h, api, clear, Credit, Key, Tag, Field, Check, Orbit, DotBar, Slider, toast, modal, selectFolder, hasNativePicker, watchJob, debounce, clearKeys, $ } from './lib.js';
import { showTutorial } from './tutorial.js';
import { renderEvent } from './event.js';

export const S = { system: null };
export const reload = () => render();
const view = () => $('#view');

// ----------------------------------------------------------------- updates
async function updateBanner(force = false) {
  try { S.update = await api.get('/api/update' + (force ? '?force=true' : '')); } catch { return null; }
  drawBanner();
  return S.update;
}

function drawBanner() {
  const box = $('#banner');
  if (!box) return;
  clear(box);
  const u = S.update;
  if (!u || !u.available || u.dismissed) return;
  const bar = DotBar({ n: 40 }); bar.classList.add('fill', 'hidden');
  const msg = h('div', { class: 'data subtle' });
  const notes = Key(t('update.whatsnew'), { h: 40, onclick: () => modal({ title: t('update.ready', { v: u.latest }), wide: true, body: h('pre', { class: 'notes' }, u.notes || t('update.notes.none')) }) });
  const later = Key(t('update.later'), { h: 40, onclick: async () => { await api.put('/api/settings', { update_dismissed: u.latest }); u.dismissed = true; drawBanner(); } });
  const now = Key(u.can_install ? t('update.now') : t('update.download'), { primary: true, h: 40, onclick: async () => {
    if (!u.can_install) return api.post('/api/open-release-page');
    now.disabled = later.disabled = true; bar.classList.remove('hidden'); msg.classList.remove('err');
    try {
      const job = await api.post('/api/update/install');
      msg.textContent = t('update.downloading');
      const done = await watchJob(job, (j) => { bar.set(j.total ? j.done / j.total : 0); });
      msg.textContent = done.result?.restarting ? t('update.restarting') : t('common.done');
    } catch (e) { msg.textContent = t('update.failed', { e: e.message }); msg.classList.add('err'); now.disabled = later.disabled = false; }
  } });
  box.append(h('div', { class: 'update-bar' }, h('div', { class: 'panel raised' }, h('div', { class: 'panel-in stack' },
    h('div', { class: 'row wrap' }, Tag(t('update.new'), { kind: 'live' }), h('div', { class: 'title grow' }, t('update.ready', { v: u.latest })), notes, later, now),
    u.can_install ? null : h('div', { class: 'data subtle' }, t('update.manual.' + (u.reason || 'no_asset'))), bar, msg))));
}

// -------------------------------------------------------------- tutorial
let tutorialChecked = false;
function firstRunTutorial(r) {
  if (tutorialChecked || r.name === 'setup' || !S.system.ready) return;
  tutorialChecked = true;
  if (!S.system.settings.tutorial_seen) showTutorial({ first: true });
}

// -------------------------------------------------------------- status bar
function startStatusBar() {
  const dev = h('span', { class: 'ms-tag' }, '…');
  const msg = h('div', { class: 'sb-msg' }, t('status.ready'));
  const bar = DotBar({ n: 28 }); bar.classList.add('mini', 'fill');
  const el = h('footer', { id: 'statusbar', role: 'status' }, dev, h('div', { class: 'sb-main' }, msg, bar));
  document.body.append(el);
  const draw = (s) => {
    dev.textContent = s.device; dev.style.color = s.device === 'CPU' ? 'var(--text-subtle)' : 'var(--cobalt-text)';
    dev.title = t('status.device', { d: s.device });
    const j = s.active[0];
    el.classList.toggle('busy', !!j);
    if (j) {
      msg.textContent = [j.label, j.message].filter(Boolean).join(' · ') + (j.eta ? ` · ${t('photos.eta', { t: fmtTime(j.eta) })}` : '');
      bar.set(j.total ? j.done / j.total : 0); bar.classList.remove('hidden');
    } else if (s.last) {
      msg.textContent = s.last.state === 'error' ? `${s.last.label} · ${t('common.error')}` : `✓ ${s.last.label}`;
      bar.classList.add('hidden');
    } else { msg.textContent = t('status.ready'); bar.classList.add('hidden'); }
  };
  const tick = () => api.get('/api/status').then(draw).catch(() => { msg.textContent = t('status.offline'); });
  setInterval(tick, 1500); tick();
  return el;
}

// ---------------------------------------------------------------- routing
function route() {
  const parts = (location.hash.replace(/^#\/?/, '') || '').split('/').filter(Boolean);
  return { name: parts[0] || 'home', args: parts.slice(1) };
}
export const go = (path) => { location.hash = '#/' + path; };

export async function refreshSystem() {
  S.system = await api.get('/api/system');
  setLang(S.system.settings.language);
  return S.system;
}

async function render() {
  const r = route();
  clearKeys();
  document.body.dataset.tab = r.name === 'e' ? (r.args[1] || 'overview') : r.name;
  try {
    if (!S.system) await refreshSystem();
    if (!S.system.ready && r.name !== 'setup') return go('setup');
    shell(r);
    if (S.update) drawBanner(); else updateBanner();
    const v = view();
    clear(v);
    if (r.name === 'setup') v.append(await setupView());
    else if (r.name === 'settings') v.append(await settingsView());
    else if (r.name === 'e') v.append(await renderEvent(r.args[0], r.args[1] || 'overview'));
    else v.append(await homeView());
    firstRunTutorial(r);
  } catch (e) {
    console.error(e);
    clear(view()).append(h('div', { class: 'empty' }, h('div', { class: 'display-l' }, '…'), h('p', { class: 'err' }, e.message || t('common.error')),
      Key(t('common.retry'), { onclick: render })));
  }
}

function shell(r) {
  const root = $('#app');
  if (!root.dataset.built || root.dataset.lang !== getLang() || root.dataset.route !== r.name) {
    clear(root);
    root.dataset.built = '1'; root.dataset.lang = getLang(); root.dataset.route = r.name;
    root.append(
      h('div', { class: 'dotfield' }),
      h('header', { class: 'top' },
        h('button', { class: 'wm', type: 'button', onclick: () => go(''), 'aria-label': '3EDIČ' }, h('img', { src: '/brand/logo.svg', alt: '3EDIČ' })),
        S.system.ready && h('nav', null,
          Tag(t('nav.events'), { onclick: () => go(''), pressed: r.name === 'home' || r.name === 'e' }),
          Tag(t('nav.settings'), { onclick: () => go('settings'), pressed: r.name === 'settings' })),
        h('div', { class: 'spacer' }),
        h('div', { class: 'right' },
          S.system.ready && Tag(t('nav.help'), { onclick: () => showTutorial(), title: t('tutorial.title') }),
          ['en', 'sk'].map((l) => Tag(l.toUpperCase(), { pressed: getLang() === l, onclick: () => changeLang(l) })))),
      h('div', { id: 'banner' }),
      h('main', { id: 'view' }),
      h('footer', { class: 'foot' }, Credit()));
  }
}

async function changeLang(l) {
  await api.put('/api/settings', { language: l });
  S.system.settings.language = l;
  setLang(l);
  $('#app').dataset.built = '';
  render();
}

// -------------------------------------------------------------------- home
async function homeView() {
  const projects = await api.get('/api/projects');
  const newKey = Key(t('home.new'), { primary: true, onclick: () => newEvent(), h: 56, legend: '+' });
  const list = projects.length
    ? h('div', { class: 'cols c3' }, projects.map((p) => projectCard(p)))
    : h('div', { class: 'empty' }, h('p', { class: 'body-l muted' }, t('home.empty')));
  return h('div', null,
    h('section', { class: 'hero' }, h('h1', { class: 'hero-logo' }, h('img', { src: '/brand/logo.svg', alt: '3EDIČ' })), h('p', { class: 'body-l muted' }, t('app.tagline')), h('div', null, newKey)),
    list);
}

function projectCard(p) {
  const f = p.photos ? p.processed / p.photos : 0;
  const bar = DotBar({ value: f, n: 24 });
  bar.classList.add('mini');
  const del = h('button', { class: 'x ms-caption', type: 'button', style: { background: 'none', border: 0, cursor: 'pointer' }, onclick: (e) => { e.stopPropagation(); confirmDelete(p); } }, t('common.delete'));
  const card = h('div', { class: 'panel clickable', role: 'link', tabindex: 0, onclick: () => go(`e/${p.id}/overview`), onkeydown: (e) => { if (e.key === 'Enter') go(`e/${p.id}/overview`); } },
    h('div', { class: 'panel-in proj' },
      h('div', { class: 'nm' }, p.name),
      h('div', { class: 'pth data' }, p.photos_dir),
      bar,
      h('div', { class: 'row' }, h('div', { class: 'num sm' }, pad3(p.photos)), h('div', { class: 'ms-caption grow' }, t('common.photos')), del)));
  return card;
}

function confirmDelete(p) {
  const m = modal({
    title: t('home.delete.title'), body: h('p', { class: 'muted' }, t('home.delete.body', { name: p.name })),
    actions: [Key(t('common.cancel'), { onclick: () => m.close() }),
      Key(t('common.delete'), { primary: true, onclick: async () => { await api.del(`/api/projects/${p.id}`); m.close(); render(); } })],
  });
}

let newEventOpen = null;                      // {setFolder} while the dialog is open: a dropped folder goes into it
function newEvent(prefill) {
  if (newEventOpen) { if (prefill) newEventOpen.setFolder(prefill); return; }
  let folder = '', roster = false;
  const name = Field({ label: t('new.name'), placeholder: t('new.name.ph'), id: 'nm', maxlength: 60 });
  const pathEl = h('div', { class: 'data muted' }, t('new.drop.none'));
  const drop = h('div', { class: 'drop', role: 'button', tabindex: 0, 'data-own-drop': '1' }, h('div', null, hasNativePicker() ? t('new.drop') : t('new.drop.browser')), pathEl);
  const setFolder = async (info) => {
    folder = info.path; pathEl.textContent = `${info.path} · ${t('new.here', { n: info.images })}`; drop.classList.add('has');
    if (!name.value) name.value = info.name;
    create.disabled = !info.images;
    if (!info.images) toast(t('new.noimages'));
  };
  const browse = async () => {
    const p = await selectFolder({ start: folder, title: t('new.pick') });
    if (p) try { setFolder(await api.post('/api/resolve-folder', { path: p })); } catch (e) { toast(e.message); }
  };
  drop.addEventListener('click', browse);
  drop.addEventListener('keydown', (e) => { if (e.key === 'Enter') browse(); });
  const create = Key(t('new.create'), { primary: true, disabled: true, onclick: async () => {
    try { const p = await api.post('/api/projects', { name: name.value, photos_dir: folder, roster_mode: roster ? 'list' : 'auto' }); m.close(); go(`e/${p.id}/overview`); } catch (e) { toast(e.message); }
  } });
  const m = modal({
    title: t('new.title'), onclose: () => { newEventOpen = null; },
    body: h('div', { class: 'stack' }, drop, name.el,
      Check({ label: t('new.names'), onchange: (v) => { roster = v; } }), h('p', { class: 'data subtle' }, t('new.names.hint')), h('p', { class: 'data subtle' }, t('new.hint'))),
    actions: [Key(t('common.cancel'), { onclick: () => m.close() }), create],
  });
  newEventOpen = { setFolder };
  if (prefill) setFolder(prefill);
}

/** A folder dropped anywhere on the window starts a new event. (A browser cannot see a folder's path; the app window can.) */
function installDrop() {
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
  window.addEventListener('dragover', (e) => { if (hasFiles(e)) { e.preventDefault(); document.body.classList.add('dragging'); } });
  window.addEventListener('dragleave', (e) => { if (!e.relatedTarget) document.body.classList.remove('dragging'); });
  window.addEventListener('drop', async (e) => {
    document.body.classList.remove('dragging');
    if (!hasFiles(e)) return;
    e.preventDefault();
    if (e.target.closest?.('.drop:not([data-own-drop])')) return;       // the Find tab's own drop zone takes photos
    const f = e.dataTransfer.files[0];
    const path = f && (f.pywebviewFullPath || f.path);
    if (!path) { toast(t('new.drop.nopath')); return; }
    try { newEvent(await api.post('/api/resolve-folder', { path })); } catch (err) { toast(err.message); }
  });
}

// ------------------------------------------------------------------- setup
const mb = (bytes) => (bytes < 1e6 ? '<1' : String(Math.round(bytes / 1e6)));

async function setupView() {
  await refreshSystem();
  const sys = S.system;
  const required = sys.models.filter((m) => m.required);
  const arc = sys.models.find((m) => m.id === 'arcface');
  let wantArc = false, accepted = false;
  const bar = DotBar({ value: 0, n: 48 });
  bar.classList.add('fill');
  const pct = h('div', { class: 'num' }, '000');
  const msg = h('div', { class: 'ms-caption' });
  const progress = h('div', { class: 'stack hidden' }, h('div', { class: 'row' }, pct, h('div', { class: 'grow' }, bar)), msg);

  const goKey = Key(t('setup.go'), { primary: true, h: 56, onclick: start });
  const licenseRow = Check({ label: t('setup.arc.license'), onchange: (v) => { accepted = v; sync(); } });
  const orbit = Orbit({ on: t('common.on'), off: t('common.off'), label: t('setup.arc.title'), onchange: (v) => { wantArc = v; licenseRow.classList.toggle('hidden', !v); sync(); } });
  licenseRow.classList.add('hidden');
  const sync = () => { goKey.disabled = wantArc && !accepted; };

  async function start() {
    goKey.disabled = true;
    progress.classList.remove('hidden');
    try {
      const ids = required.map((m) => m.id).concat(wantArc ? ['arcface'] : []);
      const job = await api.post('/api/models/install', { ids, accept: wantArc ? ['arcface'] : [] });
      await watchJob(job, (j) => {
        const f = j.total ? j.done / j.total : 0;
        bar.set(f); pct.textContent = pad3(Math.round(f * 100)); msg.textContent = `${t('setup.downloading')} · ${j.message || ''}`;
      });
      if (wantArc) await api.put('/api/settings', { use_arcface: true });
      bar.set(1); pct.textContent = '100'; msg.textContent = t('setup.ready');
      await refreshSystem();
      toast(t('setup.ready'));
      setTimeout(() => go(''), 600);
    } catch (e) {
      msg.textContent = `${t('setup.failed')} ${e.message}`; msg.classList.add('err');
      goKey.disabled = false;
    }
  }

  const hw = sys.hardware;
  return h('div', { class: 'setup' },
    h('h1', { class: 'display-l' }, t('setup.title')),
    h('p', { class: 'body-l muted' }, t('setup.body', { mb: mb(required.reduce((a, m) => a + m.size, 0)) })),
    h('div', { class: 'panel' }, h('div', { class: 'panel-in' },
      h('div', { class: 'ms-caption' }, t('setup.required')),
      required.map((m) => h('div', { class: 'mrow' }, h('div', null, h('div', { class: 't' }, m.title), h('div', { class: 'd' }, m.purpose)), h('div', { class: 'data subtle' }, `${mb(m.size)} MB`))),
      h('div', { class: 'row', style: { marginTop: '16px' } }, Tag(hw.accelerated ? hw.device : 'CPU', { kind: hw.accelerated ? 'cobalt' : 'muted' }),
        h('span', { class: 'data subtle' }, hw.accelerated ? t('setup.hw.gpu') : t('setup.hw.cpu'))))),
    arc && h('div', { class: 'panel raised' }, h('div', { class: 'panel-in stack' },
      h('div', { class: 'row top-a gap-6' }, orbit, h('div', { class: 'grow' }, h('div', { class: 'ms-caption' }, t('setup.optional')), h('div', { class: 'title', style: { margin: '4px 0' } }, t('setup.arc.title')),
        h('p', { class: 'muted' }, t('setup.arc.body', { mb: mb(arc.size) })))),
      licenseRow)),
    progress,
    h('div', null, goKey));
}

// ---------------------------------------------------------------- settings
function devicePicker(sys) {
  const hw = sys.hardware;
  const row = h('div', { class: 'tags' });
  const note = h('p', { class: 'data subtle' });
  let pref = sys.settings.device;
  const draw = () => {
    clear(row).append(...['auto', 'cpu', 'gpu'].map((k) => {
      const tag = Tag(t('settings.device.' + k), { pressed: pref === k, onclick: async () => {
        if (k === 'gpu' && !hw.gpu_available) { toast(t('settings.device.nogpu')); return; }
        pref = k; sys.settings.device = k; await api.put('/api/settings', { device: k }); toast(t('settings.device.saved')); draw();
      } });
      if (k === 'gpu' && !hw.gpu_available) tag.style.opacity = '.45';
      return tag;
    }));
    note.textContent = hw.gpu_available ? t('settings.device.found', { d: hw.gpu_providers.join(', ') }) : t('settings.device.none');
  };
  draw();
  return h('div', { class: 'stack', style: { gap: '10px' } }, row, note);
}

function dataDirPicker() {
  const box = h('div', { class: 'stack', style: { gap: '10px' } }, h('p', { class: 'data subtle' }, '…'));
  api.get('/api/data-dir').then((d) => {
    clear(box).append(h('div', { class: 'data muted', style: { wordBreak: 'break-all' } }, d.current),
      d.from_env ? h('p', { class: 'data subtle' }, t('settings.data.env'))
        : h('div', { class: 'row wrap' }, Key(t('settings.data.change'), { h: 40, onclick: () => changeDataDir(d) }), d.custom && Key(t('settings.data.reset'), { h: 40, onclick: () => moveData(d.default, d) })),
      h('p', { class: 'data subtle' }, t('settings.data.hint')));
  });
  return box;
}

async function changeDataDir(info) {
  const dest = await selectFolder({ start: info.current, title: t('settings.data.pick') });
  if (dest) moveData(dest, info);
}

function moveData(dest, info) {
  let move = true;
  const choices = h('div', { class: 'stack' });
  const paint = () => clear(choices).append(...[true, false].map((v) => h('button', { class: 'choice', type: 'button', 'aria-pressed': String(move === v), onclick: () => { move = v; paint(); } },
    h('span', { class: 't' }, t(v ? 'settings.data.move' : 'settings.data.fresh')), h('span', { class: 'd' }, t(v ? 'settings.data.move.d' : 'settings.data.fresh.d')))));
  paint();
  const m = modal({ title: t('settings.data.change'), body: h('div', { class: 'stack' }, h('div', { class: 'data muted', style: { wordBreak: 'break-all' } }, dest), choices),
    actions: [Key(t('common.cancel'), { onclick: () => m.close() }), Key(t('common.apply'), { primary: true, onclick: async () => {
      const bar = DotBar({ n: 40 }); bar.classList.add('fill');
      const msg = h('div', { class: 'data subtle' });
      clear(m.el.querySelector('.modal-body')).append(h('div', { class: 'stack' }, bar, msg));
      m.el.querySelector('.modal-actions')?.remove();
      try {
        const job = await api.post('/api/data-dir', { path: dest, move });
        await watchJob(job, (j) => { bar.set(j.total ? j.done / j.total : 0); msg.textContent = j.message || ''; });
        clear(m.el.querySelector('.modal-body')).append(h('div', { class: 'stack' }, h('p', { class: 'muted' }, t('settings.data.done')),
          h('div', null, Key(t('settings.data.restart'), { primary: true, onclick: async () => { await api.post('/api/restart'); msg.textContent = t('settings.data.restarting'); } })), msg));
      } catch (e) { msg.textContent = e.message; msg.classList.add('err'); }
    } })] });
}

async function settingsView() {
  await refreshSystem();
  const sys = S.system, s = sys.settings;
  const arc = sys.models.find((m) => m.id === 'arcface');
  const arcState = h('div', { class: 'stack' });
  const drawArc = () => {
    clear(arcState);
    if (arc.installed) {
      arcState.append(Orbit({ checked: s.use_arcface, on: t('common.on'), off: t('common.off'), label: t('settings.arc'), onchange: async (v) => { s.use_arcface = v; await api.put('/api/settings', { use_arcface: v }); toast(t('common.saved')); } }));
    } else {
      let ok = false;
      const bar = DotBar({ n: 40 }); bar.classList.add('fill', 'hidden');
      const go_ = Key(t('settings.arc.get'), { disabled: true, onclick: async () => {
        bar.classList.remove('hidden'); go_.disabled = true;
        try {
          const job = await api.post('/api/models/install', { ids: ['arcface'], accept: ['arcface'] });
          await watchJob(job, (j) => bar.set(j.total ? j.done / j.total : 0));
          await api.put('/api/settings', { use_arcface: true }); await refreshSystem(); render();
        } catch (e) { toast(e.message); go_.disabled = false; }
      } });
      arcState.append(h('p', { class: 'muted' }, t('setup.arc.body', { mb: mb(arc.size) })),
        Check({ label: t('setup.arc.license'), onchange: (v) => { ok = v; go_.disabled = !ok; } }), bar, h('div', null, go_));
    }
  };
  drawArc();

  // AI helper
  const providers = sys.vlm_providers;
  const labels = { anthropic: 'Claude', openai: 'ChatGPT', gemini: 'Gemini' };
  const provRow = h('div', { class: 'tags' });
  const keyBox = h('div', { class: 'stack' });
  const drawProv = () => {
    clear(provRow);
    provRow.append(Tag(t('settings.ai.off'), { pressed: !s.vlm_provider, onclick: async () => { s.vlm_provider = null; await api.put('/api/settings', { vlm_provider: null }); drawProv(); } }),
      ...providers.map((p) => Tag(labels[p] || p, { pressed: s.vlm_provider === p, onclick: async () => { s.vlm_provider = p; await api.put('/api/settings', { vlm_provider: p }); drawProv(); } })));
    clear(keyBox);
    if (!s.vlm_provider) return;
    const p = s.vlm_provider;
    const key = Field({ label: t('settings.ai.key'), type: 'password', placeholder: sys.vlm_keys[p] ? '••••••••' : t('settings.ai.key.ph'), id: 'apikey', maxlength: 200 });
    const model = Field({ label: t('settings.ai.model'), placeholder: sys.vlm_default_models[p], value: s.vlm_model || '', id: 'vmodel', maxlength: 60 });
    keyBox.append(key.el, model.el, h('p', { class: 'data subtle' }, sys.vlm_keys[p] ? t('settings.ai.stored') : ''),
      h('p', { class: 'data subtle' }, t('settings.ai.note')),
      h('div', null, Key(t('common.save'), { primary: true, onclick: async () => {
        if (key.value) { await api.put(`/api/secrets/${p}`, { api_key: key.value }); sys.vlm_keys[p] = true; key.value = ''; }
        s.vlm_model = model.value || null; await api.put('/api/settings', { vlm_model: s.vlm_model });
        toast(t('common.saved')); drawProv();
      } })));
  };
  drawProv();

  const upStatus = h('div', { class: 'data subtle' });
  const upPanel = h('div', { class: 'panel' }, h('div', { class: 'panel-in sect' }, h('div', { class: 'ms-caption' }, t('settings.updates')),
    h('div', { class: 'kv' }, h('span', null, t('settings.version')), h('span', { class: 'data' }, sys.version)),
    Orbit({ checked: s.check_updates, on: t('common.on'), off: t('common.off'), label: t('settings.updates.auto'), onchange: async (v) => { s.check_updates = v; await api.put('/api/settings', { check_updates: v }); } }),
    h('div', { class: 'ms-caption', style: { textAlign: 'center' } }, t('settings.updates.auto')),
    h('div', { class: 'row' }, Key(t('settings.updates.check'), { onclick: async () => {
      const u = await updateBanner(true);
      upStatus.textContent = !u || u.error ? t('settings.updates.error', { e: u?.error || '' }) : u.available ? t('settings.updates.available', { v: u.latest }) : t('settings.updates.uptodate');
    } }), upStatus),
    h('p', { class: 'data subtle' }, t('settings.updates.note'))));

  return h('div', { class: 'stack gap-8' },
    h('h1', { class: 'display-l' }, t('settings.title')),
    h('div', { class: 'cols' },
      h('div', { class: 'panel' }, h('div', { class: 'panel-in stack' },
        h('div', { class: 'sect' }, h('div', { class: 'ms-caption' }, t('settings.language')), h('div', { class: 'tags' }, ['en', 'sk'].map((l) => Tag(l === 'en' ? 'English' : 'Slovenčina', { pressed: getLang() === l, onclick: () => changeLang(l) })))),
        h('hr', { class: 'hr' }),
        h('div', { class: 'sect' }, h('div', { class: 'ms-caption' }, t('settings.hw')), devicePicker(sys), h('hr', { class: 'hr' })),
        h('div', { class: 'sect' }, h('div', { class: 'ms-caption' }, t('settings.data')), dataDirPicker(), h('hr', { class: 'hr' })),
        h('div', { class: 'sect' }, h('div', { class: 'ms-caption' }, t('settings.tagging')),
          Slider({ label: t('settings.context'), min: 2, max: 12, value: s.context_frames, format: (v) => String(v), onchange: debounce((v) => { s.context_frames = v; api.put('/api/settings', { context_frames: v }).then(() => toast(t('common.saved'))); }, 400) }),
          h('p', { class: 'data subtle' }, t('settings.context.hint')),
          h('div', null, Key(t('tutorial.replay'), { h: 40, onclick: () => showTutorial() }))),
        h('hr', { class: 'hr' }),
        h('div', { class: 'sect' }, h('div', { class: 'ms-caption' }, t('settings.models')), sys.models.map((m) => h('div', { class: 'kv' }, h('span', null, m.title), h('span', { class: 'data subtle' }, m.installed ? `${mb(m.size)} MB` : '—'))))),
      ),
      h('div', { class: 'stack gap-6' },
        arc && h('div', { class: 'panel' }, h('div', { class: 'panel-in sect' }, h('div', { class: 'ms-caption' }, t('settings.arc')), arcState)),
        h('div', { class: 'panel' }, h('div', { class: 'panel-in sect' }, h('div', { class: 'ms-caption' }, t('settings.ai')), provRow, keyBox)),
        upPanel)),
    h('div', { class: 'panel' }, h('div', { class: 'panel-in stack' }, h('div', { class: 'ms-caption' }, t('settings.aboutapp')),
      h('div', { class: 'row wrap' }, Credit({ size: 34 })), h('p', { class: 'data subtle' }, `3EDIČ ${sys.version} · AGPL-3.0`), h('p', { class: 'data subtle' }, t('settings.about')))));
}

installDrop();
startStatusBar();
window.addEventListener('hashchange', render);
setInterval(() => updateBanner(), 6 * 3600 * 1000);
render();
