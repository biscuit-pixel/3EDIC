// One event: header with live numbers and the tabs Overview / Check / Tag / Albums / Find / Export.
import { t, pad3 } from './i18n.js';
import { h, api, img, clear, Key, Tag, Field, DotBar, Slider, toast, selectFolder, watchJob, burstAt, clearKeys } from './lib.js';
import { go, reload } from './app.js';
import { renderOverview } from './workflow.js';
import { renderCheck } from './review.js';
import { renderTag } from './tag.js';
import { renderAlbums } from './albums.js';

const TABS = ['overview', 'check', 'tag', 'albums', 'find', 'export'];

export async function renderEvent(pid, tab) {
  const project = await api.get(`/api/projects/${pid}`);
  const st = project.stats;
  const root = h('div');
  const head = h('div', { class: 'event-head' },
    h('div', { class: 'stack', style: { gap: '8px' } }, h('div', { class: 'ms-caption' }, project.photos_dir), h('h1', null, project.name)),
    h('div', { class: 'stats' },
      stat(t('stat.photos'), project.photos), stat(t('stat.sorted'), st.resolved_photos), stat(t('stat.check'), st.to_check_pairs), stat(t('stat.unknown'), st.to_tag)));
  const tabs = h('div', { class: 'tabs', role: 'tablist' }, TABS.map((k) => Tag(t('tab.' + k), { pressed: (TABS.includes(tab) ? tab : 'overview') === k, onclick: () => go(`e/${pid}/${k}`) })));
  const body = h('div');
  root.append(head, tabs, body);
  const ctx = { pid, project, body, reload };
  const views = { overview: renderOverview, check: renderCheck, tag: renderTag, albums: renderAlbums, find: findTab, export: exportTab };
  clearKeys();
  body.append(await (views[tab] || renderOverview)(ctx));
  return root;
}

function stat(label, n) { return h('div', { class: 'stat' }, h('div', { class: 'ms-caption' }, label), h('div', { class: 'num' }, pad3(n))); }

// Find a running job of this kind for this event (the page may have been reloaded while it ran)
async function runningJob(kind, label) {
  const jobs = await api.get('/api/jobs');
  return jobs.find((j) => j.kind === kind && j.state === 'running' && (!label || j.label === label));
}

// -------------------------------------------------------------------- find
async function findTab({ pid }) {
  const comps = await api.get(`/api/projects/${pid}/competitors`);
  let files = [], matches = [], selected = new Set();
  const input = h('input', { type: 'file', accept: 'image/*', multiple: true, class: 'hidden' });
  const drop = h('div', { class: 'drop', tabindex: 0, role: 'button' }, t('find.drop'));
  const thumbs = h('div', { class: 'thumbs' });
  const results = h('div', { class: 'stack' });
  const go_ = Key(t('find.go'), { primary: true, disabled: true, onclick: search });
  const addFiles = (fl) => {
    files = [...fl].filter((f) => f.type.startsWith('image/')).slice(0, 10);
    clear(thumbs).append(...files.map((f) => h('div', { class: 'ph' }, h('img', { src: URL.createObjectURL(f), alt: '' }))));
    go_.disabled = !files.length;
  };
  drop.addEventListener('click', () => input.click());
  drop.addEventListener('keydown', (e) => { if (e.key === 'Enter') input.click(); });
  input.addEventListener('change', () => addFiles(input.files));
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); addFiles(e.dataTransfer.files); });

  async function search() {
    go_.disabled = true; clear(results).append(h('p', { class: 'muted' }, t('find.running') + '…'));
    const fd = new FormData(); files.forEach((f) => fd.append('files', f));
    try { const r = await fetch(`/api/projects/${pid}/find`, { method: 'POST', body: fd, credentials: 'same-origin' }); if (!r.ok) throw new Error((await r.json()).detail || r.statusText); matches = (await r.json()).matches; }
    catch (e) { clear(results).append(h('p', { class: 'err' }, e.message)); go_.disabled = false; return; }
    go_.disabled = false; selected = new Set(); showResults();
  }

  function showResults() {
    clear(results);
    const lo = matches.length ? Math.floor(Math.min(...matches.map((m) => m.sim)) * 100) : 40;
    let thr = Math.max(lo, 45);
    const grid = h('div', { class: 'grid' });
    const count = h('div', { class: 'ms-caption' });
    const sel = h('div', { class: 'ms-caption', style: { color: 'var(--text)' } });
    const draw = () => {
      const shown = matches.filter((m) => m.sim * 100 >= thr);
      clear(grid);
      for (const m of shown) {
        const cell = h('button', { class: `cell${selected.has(m.photo_id) ? ' sel' : ''}`, type: 'button', onclick: () => { selected.has(m.photo_id) ? selected.delete(m.photo_id) : selected.add(m.photo_id); cell.classList.toggle('sel'); sel.textContent = t('find.selected', { n: selected.size }); } },
          h('div', { class: 'ph' }, h('img', { src: img(pid, m.photo_id, 320), loading: 'lazy', alt: '' })), h('span', { class: 'pct' }, Math.round(m.sim * 100)));
        grid.append(cell);
      }
      count.textContent = t('find.found', { n: shown.length });
      sel.textContent = t('find.selected', { n: selected.size });
      selectShown.onclick = () => { shown.forEach((m) => selected.add(m.photo_id)); draw(); };
    };
    const selectShown = Key(t('find.select'), { h: 40 });
    const compSel = h('select', { class: 'ms-select', 'aria-label': t('find.assign') }, comps.map((c) => h('option', { value: c.id }, `${c.label} ${c.name}`)));
    results.append(
      h('div', { class: 'row wrap gap-6' }, h('div', { style: { width: '260px' } }, Slider({ label: t('find.strict'), min: Math.min(lo, 45), max: 100, value: thr, onchange: (v) => { thr = v; draw(); }, format: (v) => String(v) })), count, h('div', { class: 'grow' }), sel),
      h('div', { class: 'row wrap' }, selectShown,
        Key(t('find.export'), { primary: true, onclick: async () => {
          if (!selected.size) return;
          const dest = await selectFolder({ title: t('find.pickdest') }); if (!dest) return;
          try { const job = await api.post(`/api/projects/${pid}/find/export`, { photo_ids: [...selected], dest }); await watchJob(job); toast(t('common.landed')); } catch (e) { toast(e.message); }
        } }),
        comps.length > 0 && [compSel, Key(t('find.assign'), { onclick: async () => {
          if (!selected.size) return;
          await api.post(`/api/projects/${pid}/find/assign`, { competitor_id: +compSel.value, photo_ids: [...selected] }); toast(t('common.saved'));
        } })]),
      grid);
    draw();
  }

  return h('div', { class: 'stack gap-6' },
    h('div', { class: 'stack' }, h('h2', { class: 'title' }, t('find.title')), h('p', { class: 'muted' }, t('find.body'))),
    h('div', { class: 'panel' }, h('div', { class: 'panel-in stack' }, drop, input, thumbs, h('div', null, go_))),
    results);
}

// ------------------------------------------------------------------ export
async function exportTab({ pid, project }) {
  const cfg = { assign_mode: project.settings.assign_mode, template: project.settings.folder_template, mode: project.settings.export_mode, dest: '' };
  const plan = h('div', { class: 'plan' });
  const total = h('div', { class: 'num sm' });
  const modes = h('div', { class: 'cols' });
  const how = h('div', { class: 'cols c3' });
  const destEl = h('div', { class: 'data muted' }, '—');
  const bar = DotBar({ n: 48 }); bar.classList.add('fill', 'hidden');
  const go_ = Key(t('export.go'), { primary: true, h: 52, disabled: true, onclick: run });
  const save = (k, v) => api.put(`/api/projects/${pid}/settings`, { [k]: v });

  const drawModes = () => {
    clear(modes);
    for (const m of ['smart', 'numbers_only', 'all', 'manual']) {
      modes.append(h('button', { class: 'choice', type: 'button', 'aria-pressed': String(cfg.assign_mode === m), onclick: () => { cfg.assign_mode = m; save('assign_mode', m); drawModes(); refresh(); } },
        h('span', { class: 't' }, t('export.mode.' + m)), h('span', { class: 'd' }, t('export.mode.' + m + '.d'))));
    }
    clear(how);
    for (const m of ['copy', 'hardlink', 'symlink']) {
      how.append(h('button', { class: 'choice', type: 'button', 'aria-pressed': String(cfg.mode === m), onclick: () => { cfg.mode = m; save('export_mode', m); drawModes(); } },
        h('span', { class: 't' }, t('export.' + m)), h('span', { class: 'd' }, t('export.' + m + '.d'))));
    }
  };
  async function refresh() {
    const p = await api.get(`/api/projects/${pid}/export/plan?assign_mode=${cfg.assign_mode}`);
    total.textContent = pad3(p.files);
    clear(plan);
    if (!p.folders.length) plan.append(h('p', { class: 'muted' }, t('export.noneyet')));
    for (const f of p.folders) plan.append(h('div', { class: 'r' }, h('span', { style: f.name === '_unsorted' ? { color: 'var(--text-subtle)' } : null }, f.name === '_unsorted' ? t('export.unsorted') : f.name === 'Others' ? t('album.others') : f.name), h('span', { class: 'data subtle' }, t('export.files', { n: f.count }))));
  }
  const tpl = Field({ label: t('export.template'), value: cfg.template, id: 'tpl', maxlength: 60, onInput: (v) => { cfg.template = v; } });
  tpl.input.addEventListener('change', () => save('folder_template', cfg.template));
  async function run() {
    go_.disabled = true; bar.classList.remove('hidden');
    try {
      const job = await api.post(`/api/projects/${pid}/export`, { dest: cfg.dest, mode: cfg.mode, assign_mode: cfg.assign_mode, template: cfg.template });
      const done = await watchJob(job, (j) => bar.set(j.total ? j.done / j.total : 0));
      burstAt(go_); toast(t('export.done', { n: done.result.files, d: done.result.dest }));
    } catch (e) { toast(e.message); }
    go_.disabled = false;
  }
  drawModes(); refresh();
  return h('div', { class: 'cols side' },
    h('div', { class: 'stack gap-6' },
      h('div', { class: 'panel' }, h('div', { class: 'panel-in sect' }, h('div', { class: 'ms-caption' }, t('export.mode')), modes)),
      h('div', { class: 'panel' }, h('div', { class: 'panel-in stack' }, tpl.el,
        h('div', { class: 'tags' }, ['{number:03d}', '{number:03d} {name}', '{name} {number}'].map((x) => Tag(x, { onclick: () => { tpl.value = x; cfg.template = x; save('folder_template', x); } }))),
        h('div', { class: 'ms-caption' }, t('export.how')), how))),
    h('div', { class: 'stack gap-6' },
      h('div', { class: 'panel raised' }, h('div', { class: 'panel-in stack' },
        h('div', { class: 'row' }, h('div', { class: 'title grow' }, t('export.preview')), total), plan)),
      h('div', { class: 'panel' }, h('div', { class: 'panel-in stack' },
        h('div', { class: 'ms-caption' }, t('export.dest')), destEl,
        h('div', { class: 'row' }, Key(t('export.pick'), { onclick: async () => { const d = await selectFolder({ title: t('export.pick') }); if (d) { cfg.dest = d; destEl.textContent = d; go_.disabled = false; } } }), go_), bar))));
}
