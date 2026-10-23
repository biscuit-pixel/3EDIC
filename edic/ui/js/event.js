// One event: header with live numbers, tabs, and the Photos / Competitors / Number / Find / Export tabs.
import { t, pad3, fmtTime } from './i18n.js';
import { h, api, img, crop, clear, Key, Tag, Field, DotBar, Slider, toast, pickFolder, watchJob, burstAt } from './lib.js';
import { go, S } from './app.js';
import { renderReview } from './review.js';

const TABS = ['photos', 'roster', 'number', 'review', 'find', 'export'];

export async function renderEvent(pid, tab) {
  const project = await api.get(`/api/projects/${pid}`);
  const st = project.stats;
  const root = h('div');
  const head = h('div', { class: 'event-head' },
    h('div', { class: 'stack', style: { gap: '8px' } }, h('div', { class: 'ms-caption' }, project.photos_dir), h('h1', null, project.name)),
    h('div', { class: 'stats' },
      stat(t('stat.photos'), project.photos), stat(t('stat.sorted'), st.resolved_photos), stat(t('stat.check'), st.to_check_pairs), stat(t('stat.unknown'), st.unknown_photos)));
  const tabs = h('div', { class: 'tabs', role: 'tablist' }, TABS.map((k) => Tag(t('tab.' + k), { pressed: k === tab, onclick: () => go(`e/${pid}/${k}`) })));
  const body = h('div');
  root.append(head, tabs, body);
  const ctx = { pid, project, body, reload: () => go(`e/${pid}/${tab}`) };
  const views = { photos: photosTab, roster: rosterTab, number: numberTab, review: renderReview, find: findTab, export: exportTab };
  body.append(await (views[tab] || photosTab)(ctx));
  return root;
}

function stat(label, n) { return h('div', { class: 'stat' }, h('div', { class: 'ms-caption' }, label), h('div', { class: 'num' }, pad3(n))); }

// Find a running job of this kind for this event (the page may have been reloaded while it ran)
async function runningJob(kind, label) {
  const jobs = await api.get('/api/jobs');
  return jobs.find((j) => j.kind === kind && j.state === 'running' && (!label || j.label === label));
}

// ------------------------------------------------------------------ photos
async function photosTab({ pid, project, reload }) {
  const wrap = h('div', { class: 'stack gap-6' });
  const panel = h('div', { class: 'panel' });
  const inner = h('div', { class: 'panel-in stack' });
  panel.append(inner);
  const bar = DotBar({ value: project.photos ? project.processed / project.photos : 0, n: 60 });
  bar.classList.add('fill');
  const jobBox = h('div', { class: 'job hidden' });
  const jb = DotBar({ n: 60 }); jb.classList.add('fill');
  const jmsg = h('div', { class: 'data subtle file' });
  const jeta = h('div', { class: 'ms-caption' });
  const stop = Key(t('photos.cancel'), { onclick: () => api.post(`/api/jobs/${current.id}/cancel`) });
  let current = null;
  jobBox.append(jb, h('div', { class: 'row' }, h('div', { class: 'grow' }, jmsg), jeta, stop));

  const need = project.photos === 0 || project.processed < project.photos;
  const run = Key(project.photos === 0 ? t('photos.process') : need ? t('photos.process') : t('photos.rescan'), { primary: need, h: 52, onclick: start });
  async function track(job) {
    current = job; run.disabled = true; jobBox.classList.remove('hidden');
    try {
      const done = await watchJob(job, (j) => {
        jb.set(j.total ? j.done / j.total : 0); jmsg.textContent = j.message || ''; jeta.textContent = j.eta ? t('photos.eta', { t: fmtTime(j.eta) }) : '';
      });
      toast(t('photos.done', { n: done.result?.pairs ?? 0 }));
      reload();
    } catch (e) { toast(e.message); run.disabled = false; }
  }
  async function start() { try { await track(await api.post(`/api/projects/${pid}/process`)); } catch (e) { toast(e.message); } }

  inner.append(
    h('div', { class: 'row gap-8' },
      h('div', null, h('div', { class: 'hero-num' }, pad3(project.photos)), h('div', { class: 'ms-caption' }, t('photos.found'))),
      h('div', null, h('div', { class: 'hero-num', style: { color: 'var(--text-subtle)' } }, pad3(project.processed)), h('div', { class: 'ms-caption' }, t('photos.processed')))),
    bar, jobBox, h('div', { class: 'row' }, run, project.processed > 0 && h('span', { class: 'data subtle' }, t('photos.next'))));
  wrap.append(panel);

  if (project.photos) {
    const photos = await api.get(`/api/projects/${pid}/photos?limit=48`);
    wrap.append(h('div', { class: 'grid' }, photos.map((p) => h('div', { class: 'cell', style: { cursor: 'default' } }, h('div', { class: 'ph' }, h('img', { src: img(pid, p.id, 320), loading: 'lazy', alt: p.name }))))));
  }
  const live = await runningJob('process', project.name);
  if (live) track(live);
  return wrap;
}

// ------------------------------------------------------------------ roster
async function rosterTab({ pid, reload }) {
  const comps = await api.get(`/api/projects/${pid}/competitors`);
  const suggest = await api.get(`/api/projects/${pid}/roster/suggest`);
  const area = h('textarea', { class: 'area', placeholder: t('roster.paste.ph'), 'aria-label': t('roster.paste'), spellcheck: 'false' });
  const add = Key(t('roster.add'), { primary: true, onclick: async () => {
    if (!area.value.trim()) return;
    const r = await api.post(`/api/projects/${pid}/competitors/import`, { text: area.value });
    toast(`${t('common.landed')} +${r.added}`); reload();
  } });
  const list = h('div');
  if (!comps.length) list.append(h('p', { class: 'muted' }, t('roster.empty')));
  for (const c of comps) {
    const nm = h('input', { value: c.name, placeholder: t('roster.name.ph'), 'aria-label': t('roster.name.ph') });
    nm.addEventListener('change', () => api.put(`/api/projects/${pid}/competitors/${c.id}`, { name: nm.value }).then(() => toast(t('common.saved'))));
    list.append(h('div', { class: 'comp' }, h('span', { class: 'n' }, pad3(c.number)), nm,
      h('button', { class: 'x', type: 'button', 'aria-label': t('common.delete'), onclick: async () => { await api.del(`/api/projects/${pid}/competitors/${c.id}`); reload(); } }, '×')));
  }
  return h('div', { class: 'cols side' },
    h('div', { class: 'stack' },
      h('div', { class: 'panel' }, h('div', { class: 'panel-in stack' }, h('div', { class: 'ms-caption' }, t('roster.paste')), area, h('p', { class: 'data subtle' }, t('roster.paste.hint')), h('div', null, add))),
      suggest.length > 0 && h('div', { class: 'panel' }, h('div', { class: 'panel-in sect' }, h('div', { class: 'ms-caption' }, t('roster.suggest')),
        h('div', { class: 'tags' }, suggest.slice(0, 40).map((s) => Tag(`${pad3(s.number)} · ${t('roster.suggest.n', { n: s.photos })}`, { onclick: async () => { await api.post(`/api/projects/${pid}/competitors`, { number: s.number }); reload(); } })))))),
    h('div', { class: 'panel' }, h('div', { class: 'panel-in' }, h('div', { class: 'row', style: { marginBottom: '8px' } }, h('div', { class: 'title grow' }, t('roster.title')), h('div', { class: 'ms-caption' }, t('roster.count', { n: comps.length }))), list)));
}

// ------------------------------------------------------------------ number
async function numberTab({ pid, project, reload }) {
  const cal = await api.get(`/api/projects/${pid}/calibration`);
  const wrap = h('div', { class: 'stack gap-6' });
  const stage = h('div', { class: 'stack' });
  const need = Math.max(0, 2 - cal.examples.length);
  const status = h('div', { class: 'row wrap' },
    cal.profile ? Tag(t('num.learned'), { kind: 'cobalt' }) : Tag(t('num.need', { n: need }), { kind: 'amber' }),
    cal.examples.length > 0 && h('div', { class: 'tags' }, cal.examples.map((e) => Tag(e.number != null ? pad3(e.number) : '·'))),
    cal.examples.length > 0 && Key(t('num.reset'), { h: 36, onclick: async () => { await api.del(`/api/projects/${pid}/calibration`); reload(); } }));
  const applyBar = DotBar({ n: 48 }); applyBar.classList.add('fill', 'hidden');
  const apply = Key(t('num.apply'), { primary: !!cal.profile, disabled: !cal.profile, h: 48, onclick: async () => {
    apply.disabled = true; applyBar.classList.remove('hidden');
    try {
      const job = await api.post(`/api/projects/${pid}/calibration/apply`);
      await watchJob(job, (j) => applyBar.set(j.total ? j.done / j.total : 0));
      toast(t('common.landed')); reload();
    } catch (e) { toast(e.message); apply.disabled = false; }
  } });

  const picker = h('div', { class: 'grid', style: { gridTemplateColumns: 'repeat(auto-fill,minmax(110px,1fr))' } });
  if (!cal.suggested_photos.length) picker.append(h('p', { class: 'muted' }, t('num.none')));
  cal.suggested_photos.forEach((id) => {
    const cell = h('button', { class: 'cell', type: 'button', onclick: () => { picker.querySelectorAll('.cell').forEach((c) => c.classList.remove('sel')); cell.classList.add('sel'); openMarker(id); } },
      h('div', { class: 'ph' }, h('img', { src: img(pid, id, 320), alt: '', loading: 'lazy' })));
    picker.append(cell);
  });

  async function openMarker(photoId) {
    const d = await api.get(`/api/projects/${pid}/photos/${photoId}`);
    const people = d.persons.filter((p) => p.hip).sort((a, b) => (b.box[3] - b.box[1]) - (a.box[3] - a.box[1]));
    clear(stage);
    if (!people.length) { stage.append(h('p', { class: 'muted' }, t('num.none'))); return; }
    const hip = people[0].hip, cx = (hip[0] + hip[2]) / 2, cy = (hip[1] + hip[3]) / 2;
    const hw = (hip[2] - hip[0]) * 0.85, hh = (hip[3] - hip[1]) * 1.2;
    const reg = [Math.max(0, cx - hw), Math.max(0, cy - hh), Math.min(d.width, cx + hw), Math.min(d.height, cy + hh)];
    const im = h('img', { src: crop(pid, photoId, reg, 1000), alt: '', draggable: 'false' });
    const rectEl = h('div', { class: 'box hidden' });
    const st = h('div', { class: 'mark-stage', tabindex: 0 }, im, rectEl);
    let rect = null, start = null;
    const rel = (e) => { const b = im.getBoundingClientRect(); return [Math.min(1, Math.max(0, (e.clientX - b.left) / b.width)), Math.min(1, Math.max(0, (e.clientY - b.top) / b.height))]; };
    const draw = () => {
      if (!rect) return;
      Object.assign(rectEl.style, { left: rect[0] * 100 + '%', top: rect[1] * 100 + '%', width: (rect[2] - rect[0]) * 100 + '%', height: (rect[3] - rect[1]) * 100 + '%' });
      rectEl.classList.remove('hidden');
    };
    st.addEventListener('pointerdown', (e) => { st.setPointerCapture(e.pointerId); start = rel(e); rect = [...start, ...start]; draw(); });
    st.addEventListener('pointermove', (e) => { if (!start) return; const p = rel(e); rect = [Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.max(start[0], p[0]), Math.max(start[1], p[1])]; draw(); });
    st.addEventListener('pointerup', () => { start = null; });
    const num = Field({ label: t('num.number'), id: 'bibno', maxlength: 4, inputmode: 'numeric', onEnter: () => save.click() });
    const save = Key(t('num.save'), { primary: true, onclick: async () => {
      if (!rect || rect[2] - rect[0] < 0.02) { toast(t('num.draw')); return; }
      const W = reg[2] - reg[0], H = reg[3] - reg[1];
      const box = [reg[0] + rect[0] * W, reg[1] + rect[1] * H, reg[0] + rect[2] * W, reg[1] + rect[3] * H];
      try { await api.post(`/api/projects/${pid}/calibration/example`, { photo_id: photoId, number: num.value ? +num.value : null, box }); toast(t('common.landed')); reload(); }
      catch (e) { toast(e.message); }
    } });
    stage.append(h('div', { class: 'ms-caption' }, t('num.draw')), st, h('div', { class: 'row top-a' }, h('div', { style: { width: '160px' } }, num.el), save));
    num.input.focus();
  }

  wrap.append(
    h('div', { class: 'stack' }, h('h2', { class: 'title' }, t('num.title')), h('p', { class: 'muted' }, t('num.body'))),
    h('div', { class: 'cols side' },
      h('div', { class: 'panel' }, h('div', { class: 'panel-in sect' }, h('div', { class: 'ms-caption' }, t('num.pick')), picker)),
      h('div', { class: 'panel raised' }, h('div', { class: 'panel-in stack' }, status, stage))),
    h('div', { class: 'stack' }, applyBar, h('div', null, apply)),
    await vlmPanel(pid));
  return wrap;
}

async function vlmPanel(pid) {
  const provider = S.system.settings.vlm_provider;
  const hasKey = provider && S.system.vlm_keys[provider];
  const inner = h('div', { class: 'panel-in stack' }, h('div', { class: 'title' }, t('num.vlm.title')), h('p', { class: 'muted' }, t('num.vlm.body')));
  if (!hasKey) {
    inner.append(h('p', { class: 'data subtle' }, t('num.vlm.off')), h('div', null, Key(t('nav.settings'), { onclick: () => go('settings') })));
  } else {
    const est = await api.get(`/api/projects/${pid}/vlm/estimate?provider=${provider}`);
    const bar = DotBar({ n: 48 }); bar.classList.add('fill', 'hidden');
    const msg = h('div', { class: 'data subtle' });
    const run = Key(t('num.vlm.run'), { primary: est.crops > 0, disabled: est.crops === 0, onclick: async () => {
      run.disabled = true; bar.classList.remove('hidden');
      try {
        const job = await api.post(`/api/projects/${pid}/vlm/run`);
        const done = await watchJob(job, (j) => { bar.set(j.total ? j.done / j.total : 0); msg.textContent = j.message; });
        toast(t('num.vlm.found', { n: done.result?.numbers_found ?? 0 })); go(`e/${pid}/number`);
      } catch (e) { toast(e.message); run.disabled = false; }
    } });
    inner.append(h('div', { class: 'row' }, Tag(provider), h('span', { class: 'data subtle' }, t('num.vlm.estimate', { n: est.crops, c: est.approx_cost_usd.toFixed(2) }))), bar, msg, h('div', null, run));
  }
  return h('div', { class: 'panel' }, inner);
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
    const compSel = h('select', { class: 'ms-select', 'aria-label': t('find.assign') }, comps.map((c) => h('option', { value: c.id }, `${pad3(c.number)} ${c.name}`)));
    results.append(
      h('div', { class: 'row wrap gap-6' }, h('div', { style: { width: '260px' } }, Slider({ label: t('find.strict'), min: Math.min(lo, 45), max: 100, value: thr, onchange: (v) => { thr = v; draw(); }, format: (v) => String(v) })), count, h('div', { class: 'grow' }), sel),
      h('div', { class: 'row wrap' }, selectShown,
        Key(t('find.export'), { primary: true, onclick: async () => {
          if (!selected.size) return;
          const dest = await pickFolder({ title: t('find.pickdest') }); if (!dest) return;
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
    for (const f of p.folders) plan.append(h('div', { class: 'r' }, h('span', { style: f.name === '_unsorted' ? { color: 'var(--text-subtle)' } : null }, f.name === '_unsorted' ? t('export.unsorted') : f.name), h('span', { class: 'data subtle' }, t('export.files', { n: f.count }))));
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
        h('div', { class: 'row' }, Key(t('export.pick'), { onclick: async () => { const d = await pickFolder({ title: t('export.pick') }); if (d) { cfg.dest = d; destEl.textContent = d; go_.disabled = false; } } }), go_), bar))));
}
