// 3EDIČ — entry point: router, shell, home, first-run setup and settings.
import { t, setLang, getLang, pad3, fmtTime } from './i18n.js';
import { h, api, clear, Key, Tag, Field, Check, Orbit, DotBar, toast, modal, pickFolder, watchJob, $ } from './lib.js';
import { renderEvent } from './event.js';

export const S = { system: null };
const view = () => $('#view');

// ---------------------------------------------------------------- routing
function route() {
  const parts = (location.hash.replace(/^#\/?/, '') || '').split('/').filter(Boolean);
  return { name: parts[0] || 'home', args: parts.slice(1) };
}
export const go = (path) => { location.hash = '#/' + path; };

async function refreshSystem() {
  S.system = await api.get('/api/system');
  setLang(S.system.settings.language);
  return S.system;
}

async function render() {
  const r = route();
  try {
    if (!S.system) await refreshSystem();
    if (!S.system.ready && r.name !== 'setup') return go('setup');
    shell(r);
    const v = view();
    clear(v);
    if (r.name === 'setup') v.append(await setupView());
    else if (r.name === 'settings') v.append(await settingsView());
    else if (r.name === 'e') v.append(await renderEvent(r.args[0], r.args[1] || 'photos'));
    else v.append(await homeView());
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
    const hw = S.system.hardware;
    root.append(
      h('div', { class: 'dotfield' }),
      h('header', { class: 'top' },
        h('button', { class: 'wm', type: 'button', onclick: () => go(''), 'aria-label': '3EDIČ' }, '3EDIČ'),
        S.system.ready && h('nav', null,
          Tag(t('nav.events'), { onclick: () => go(''), pressed: r.name === 'home' || r.name === 'e' }),
          Tag(t('nav.settings'), { onclick: () => go('settings'), pressed: r.name === 'settings' })),
        h('div', { class: 'spacer' }),
        h('div', { class: 'right' },
          Tag(hw.accelerated ? hw.device : 'CPU', { kind: hw.accelerated ? 'cobalt' : 'muted', title: hw.providers.join(', ') }),
          ['en', 'sk'].map((l) => Tag(l.toUpperCase(), { pressed: getLang() === l, onclick: () => changeLang(l) })))),
      h('main', { id: 'view' }));
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
  const newKey = Key(t('home.new'), { primary: true, onclick: newEvent, h: 56, legend: '+' });
  const list = projects.length
    ? h('div', { class: 'cols c3' }, projects.map((p) => projectCard(p)))
    : h('div', { class: 'empty' }, h('p', { class: 'body-l muted' }, t('home.empty')));
  return h('div', null,
    h('section', { class: 'hero' }, h('h1', { class: 'display-xl' }, '3EDIČ'), h('p', { class: 'body-l muted' }, t('app.tagline')), h('div', null, newKey)),
    list);
}

function projectCard(p) {
  const f = p.photos ? p.processed / p.photos : 0;
  const bar = DotBar({ value: f, n: 24 });
  bar.classList.add('mini');
  const del = h('button', { class: 'x ms-caption', type: 'button', style: { background: 'none', border: 0, cursor: 'pointer' }, onclick: (e) => { e.stopPropagation(); confirmDelete(p); } }, t('common.delete'));
  const card = h('div', { class: 'panel clickable', role: 'link', tabindex: 0, onclick: () => go(`e/${p.id}/photos`), onkeydown: (e) => { if (e.key === 'Enter') go(`e/${p.id}/photos`); } },
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

function newEvent() {
  let folder = '';
  const name = Field({ label: t('new.name'), placeholder: t('new.name.ph'), id: 'nm', maxlength: 60 });
  const pathEl = h('div', { class: 'data muted' }, '—');
  const pick = Key(t('new.pick'), { onclick: async () => {
    const p = await pickFolder({ start: folder });
    if (p) { folder = p; pathEl.textContent = p; if (!name.value) name.value = p.split(/[\\/]/).filter(Boolean).pop(); create.disabled = false; }
  } });
  const create = Key(t('new.create'), { primary: true, disabled: true, onclick: async () => {
    try { const p = await api.post('/api/projects', { name: name.value, photos_dir: folder }); m.close(); go(`e/${p.id}/photos`); } catch (e) { toast(e.message); }
  } });
  const m = modal({
    title: t('new.title'),
    body: h('div', { class: 'stack' }, name.el,
      h('div', { class: 'sect' }, h('div', { class: 'ms-caption' }, t('new.folder')), pathEl, h('div', null, pick), h('p', { class: 'data subtle' }, t('new.hint')))),
    actions: [Key(t('common.cancel'), { onclick: () => m.close() }), create],
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
      providers.map((p) => Tag(labels[p] || p, { pressed: s.vlm_provider === p, onclick: async () => { s.vlm_provider = p; await api.put('/api/settings', { vlm_provider: p }); drawProv(); } })));
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

  return h('div', { class: 'stack gap-8' },
    h('h1', { class: 'display-l' }, t('settings.title')),
    h('div', { class: 'cols' },
      h('div', { class: 'panel' }, h('div', { class: 'panel-in stack' },
        h('div', { class: 'sect' }, h('div', { class: 'ms-caption' }, t('settings.language')), h('div', { class: 'tags' }, ['en', 'sk'].map((l) => Tag(l === 'en' ? 'English' : 'Slovenčina', { pressed: getLang() === l, onclick: () => changeLang(l) })))),
        h('hr', { class: 'hr' }),
        h('div', { class: 'sect' }, h('div', { class: 'ms-caption' }, t('settings.hw')), h('div', { class: 'row' }, Tag(sys.hardware.accelerated ? sys.hardware.device : 'CPU', { kind: sys.hardware.accelerated ? 'cobalt' : 'muted' }), h('span', { class: 'data subtle' }, sys.hardware.providers.join(' · ')))),
        h('hr', { class: 'hr' }),
        h('div', { class: 'sect' }, h('div', { class: 'ms-caption' }, t('settings.models')), sys.models.map((m) => h('div', { class: 'kv' }, h('span', null, m.title), h('span', { class: 'data subtle' }, m.installed ? `${mb(m.size)} MB` : '—'))))),
      ),
      h('div', { class: 'stack gap-6' },
        arc && h('div', { class: 'panel' }, h('div', { class: 'panel-in sect' }, h('div', { class: 'ms-caption' }, t('settings.arc')), arcState)),
        h('div', { class: 'panel' }, h('div', { class: 'panel-in sect' }, h('div', { class: 'ms-caption' }, t('settings.ai')), provRow, keyBox)))),
    h('p', { class: 'data subtle' }, t('settings.about')));
}

window.addEventListener('hashchange', render);
render();
