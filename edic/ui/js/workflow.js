// Overview: the whole job as five steps — photos, number examples, analysis, AI helper, tagging — with the current one open.
import { t, pad3, fmtTime } from './i18n.js';
import { h, api, img, crop, clear, Key, Tag, Field, DotBar, PhotoStage, drawBox, toast, modal, watchJob, burstAt } from './lib.js';
import { go, S, refreshSystem } from './app.js';

const STAGES = ['import', 'examples', 'analyse', 'ai', 'review'];
const AI_NAMES = { anthropic: 'Claude', openai: 'ChatGPT', gemini: 'Gemini' };

export async function renderOverview(ctx) {
  const { pid, project } = ctx;
  const w = project.workflow, st = project.stats;
  const cur = STAGES.indexOf(w.stage);
  const steps = [
    { key: 'import', title: t('ov.import'), done: t('ov.import.done', { n: w.photos }), body: () => importStep(ctx) },
    { key: 'examples', title: t('ov.examples'), done: w.examples ? t('ov.examples.done', { n: w.examples }) : t('ov.skipped'), body: () => examplesStep(ctx) },
    { key: 'analyse', title: t('ov.analyse'), done: t('ov.analyse.done', { a: w.processed, b: w.photos }), body: () => analyseStep(ctx) },
    { key: 'ai', title: t('ov.ai'), done: w.ai_done ? t('ov.ai.done') : t('ov.skipped'), body: () => aiStep(ctx), optional: true },
    { key: 'review', title: t('ov.review'), done: '', body: () => reviewStep(ctx) },
  ];
  const list = h('div', { class: 'steps' });
  for (const [i, s] of steps.entries()) {
    const state = i < cur ? 'done' : i === cur ? 'current' : 'todo';
    const body = state === 'current' ? await s.body() : null;
    list.append(h('section', { class: `step ${state}`, 'data-step': s.key },
      h('div', { class: 'badge' }, state === 'done' ? '✓' : String(i + 1)),
      h('div', { class: 'step-main' },
        h('div', { class: 'row wrap' }, h('h2', { class: 'title' }, s.title), s.optional && state !== 'done' && Tag(t('ov.optional')), state === 'done' && h('span', { class: 'data subtle' }, s.done)),
        body && h('div', { class: 'step-body' }, body))));
  }
  const names = project.settings.roster_mode === 'list' ? await namesCard(ctx) : null;
  return h('div', { class: 'stack gap-6' }, names, list);
}

/** Follow a running job of this kind, or start one. Returns the job's final state. */
async function runOrFollow(kind, startUrl, onTick) {
  const jobs = await api.get('/api/jobs');
  const live = jobs.find((j) => j.kind === kind && j.state === 'running');
  return watchJob(live || await api.post(startUrl), onTick);
}

function jobBox() {
  const bar = DotBar({ n: 60 }); bar.classList.add('fill');
  const msg = h('div', { class: 'data subtle file' });
  const eta = h('div', { class: 'ms-caption' });
  const stop = Key(t('common.stop'), { h: 36 });
  const el = h('div', { class: 'job' }, bar, h('div', { class: 'row' }, h('div', { class: 'grow' }, msg), eta, stop));
  return { el, stop, tick: (j) => { bar.set(j.total ? j.done / j.total : 0); msg.textContent = j.message || ''; eta.textContent = j.eta ? t('photos.eta', { t: fmtTime(j.eta) }) : ''; } };
}

// ------------------------------------------------------------------ 1. photos
function importStep(ctx) {
  const jb = jobBox(); jb.stop.classList.add('hidden');
  const box = h('div', { class: 'stack' }, h('p', { class: 'muted' }, t('ov.import.body', { dir: ctx.project.photos_dir })), jb.el);
  runOrFollow('ingest', `/api/projects/${ctx.pid}/ingest`, jb.tick)
    .then(() => ctx.reload()).catch((e) => clear(box).append(h('p', { class: 'err' }, e.message), Key(t('common.retry'), { onclick: ctx.reload })));
  return box;
}

// ----------------------------------------------------------- 2. number examples
function examplesStep(ctx) {
  const box = h('div', { class: 'stack' });
  const w = ctx.project.workflow;
  const skip = Key(t('ov.examples.skip'), { h: 40, onclick: async () => { await api.post(`/api/projects/${ctx.pid}/workflow/skip`, { step: 'examples' }); ctx.reload(); } });
  if (w.sampled === 0) {                                          // look at a dozen photos first, so there is something to point at
    const jb = jobBox();
    box.append(h('p', { class: 'muted' }, t('ov.examples.preparing')), jb.el);
    jb.stop.onclick = () => ctx.reload();
    runOrFollow('prepare', `/api/projects/${ctx.pid}/prepare`, jb.tick).then(() => ctx.reload()).catch((e) => clear(box).append(h('p', { class: 'err' }, e.message), skip));
    return box;
  }
  examplesPanel(ctx, {}).then((p) => box.append(p, h('div', { class: 'row' }, skip, h('span', { class: 'data subtle' }, t('ov.examples.skip.hint')))));
  return box;
}

/** Pick a photo, drag a box around the number, type it. Also used (in a dialog) to teach more numbers later. */
export async function examplesPanel(ctx, { onChange } = {}) {
  const { pid } = ctx;
  const cal = await api.get(`/api/projects/${pid}/calibration`);
  const stage = h('div', { class: 'stack' });
  const need = Math.max(0, 2 - cal.examples.length);
  const changed = () => (onChange ? onChange() : ctx.reload());
  const status = h('div', { class: 'row wrap' },
    cal.profile ? Tag(t('num.learned'), { kind: 'cobalt' }) : Tag(t('num.need', { n: need }), { kind: 'amber' }),
    cal.examples.length > 0 && h('div', { class: 'tags' }, cal.examples.map((e) => Tag(e.number != null ? pad3(e.number) : '·'))),
    cal.examples.length > 0 && Key(t('num.reset'), { h: 36, onclick: async () => { await api.del(`/api/projects/${pid}/calibration`); changed(); } }));

  const picker = h('div', { class: 'grid small' });
  if (!cal.suggested_photos.length) picker.append(h('p', { class: 'muted' }, t('num.none')));
  for (const id of cal.suggested_photos) {
    const cell = h('button', { class: 'cell', type: 'button', onclick: () => { picker.querySelectorAll('.cell').forEach((c) => c.classList.remove('sel')); cell.classList.add('sel'); openMarker(id); } },
      h('div', { class: 'ph' }, h('img', { src: img(pid, id, 320), alt: '', loading: 'lazy' })));
    picker.append(cell);
  }

  async function openMarker(photoId) {
    const d = await api.get(`/api/projects/${pid}/photos/${photoId}`);
    const people = d.persons.filter((p) => p.hip).sort((a, b) => (b.box[3] - b.box[1]) - (a.box[3] - a.box[1]));
    clear(stage);
    if (!people.length) { stage.append(h('p', { class: 'muted' }, t('num.none'))); return; }
    const hip = people[0].hip, cx = (hip[0] + hip[2]) / 2, cy = (hip[1] + hip[3]) / 2;
    const hw = (hip[2] - hip[0]) * 0.85, hh = (hip[3] - hip[1]) * 1.2;
    const reg = [Math.max(0, cx - hw), Math.max(0, cy - hh), Math.min(d.width, cx + hw), Math.min(d.height, cy + hh)];
    const ps = PhotoStage({ src: `/api/projects/${pid}/photos/${photoId}/crop?box=${reg.map(Math.round).join(',')}&w=1000`, width: reg[2] - reg[0], height: reg[3] - reg[1], maxVh: 56 });
    const marker = drawBox(ps);
    const num = Field({ label: t('num.number'), id: 'bibno', maxlength: 4, inputmode: 'numeric', onEnter: () => save.click() });
    const save = Key(t('num.save'), { primary: true, onclick: async () => {
      const r = marker.rect;
      if (!r || r[2] - r[0] < 0.02) { toast(t('num.draw')); return; }
      const W = reg[2] - reg[0], H = reg[3] - reg[1];
      const box = [reg[0] + r[0] * W, reg[1] + r[1] * H, reg[0] + r[2] * W, reg[1] + r[3] * H];
      try { await api.post(`/api/projects/${pid}/calibration/example`, { photo_id: photoId, number: num.value ? +num.value : null, box }); toast(t('common.landed')); changed(); }
      catch (e) { toast(e.message); }
    } });
    stage.append(h('div', { class: 'ms-caption' }, t('num.draw')), ps.el, h('div', { class: 'row top-a' }, h('div', { style: { width: '160px' } }, num.el), save));
    num.input.focus();
  }

  const out = h('div', { class: 'cols side' },
    h('div', { class: 'stack' }, h('div', { class: 'ms-caption' }, t('num.pick')), picker),
    h('div', { class: 'panel raised' }, h('div', { class: 'panel-in stack' }, status, stage)));
  return h('div', { class: 'stack' }, h('p', { class: 'muted' }, t('num.body')), out);
}

// ------------------------------------------------------------------ 3. analysis
function analyseStep(ctx) {
  const { pid, project } = ctx;
  const w = project.workflow;
  const jb = jobBox(); jb.el.classList.add('hidden');
  const run = Key(t('photos.process'), { primary: true, h: 52, legend: String(w.photos - w.processed), onclick: start });
  let current = null;
  async function track(job) {
    current = job; run.disabled = true; jb.el.classList.remove('hidden');
    jb.stop.onclick = () => api.post(`/api/jobs/${current.id}/cancel`);
    try {
      const done = await watchJob(job, jb.tick);
      toast(t('photos.done', { n: done.result?.pairs ?? 0 }));
      ctx.reload();
    } catch (e) { toast(e.message); run.disabled = false; }
  }
  async function start() { try { await track(await api.post(`/api/projects/${pid}/process`)); } catch (e) { toast(e.message); } }
  api.get('/api/jobs').then((jobs) => { const live = jobs.find((j) => j.kind === 'process' && j.state === 'running'); if (live) track(live); });
  return h('div', { class: 'stack' }, h('p', { class: 'muted' }, t('ov.analyse.body', { n: w.photos })), h('div', { class: 'row' }, run, h('span', { class: 'data subtle' }, S.system.hardware.accelerated ? t('ov.analyse.gpu', { d: S.system.hardware.device }) : t('ov.analyse.cpu'))), jb.el);
}

// ----------------------------------------------------------------- 4. AI helper
function aiStep(ctx) {
  const box = h('div', { class: 'stack' });
  const skip = Key(t('ov.ai.skip'), { h: 40, onclick: async () => { await api.post(`/api/projects/${ctx.pid}/workflow/skip`, { step: 'ai' }); ctx.reload(); } });
  box.append(h('p', { class: 'muted' }, t('ov.ai.body', { n: ctx.project.stats.to_tag })));
  aiPanel(ctx, { onDone: () => ctx.reload() }).then((p) => box.append(p, h('div', { class: 'row' }, skip, h('span', { class: 'data subtle' }, t('ov.ai.skip.hint')))));
  return box;
}

/** Choose Claude / ChatGPT / Gemini, paste the key, see the price, run. Used on the overview and in a dialog. */
export async function aiPanel(ctx, { onDone } = {}) {
  const { pid } = ctx;
  const host = h('div', { class: 'stack' });
  async function draw(edit = false) {
    clear(host);
    const sys = S.system, s = sys.settings;
    const provider = s.vlm_provider, hasKey = provider && sys.vlm_keys[provider];
    if (!hasKey || edit) {
      let chosen = provider || sys.vlm_providers[0];
      const tags = h('div', { class: 'tags' });
      const key = Field({ label: t('settings.ai.key'), type: 'password', placeholder: t('settings.ai.key.ph'), id: 'aikey', maxlength: 200 });
      const paint = () => { clear(tags).append(...sys.vlm_providers.map((p) => Tag(AI_NAMES[p] || p, { pressed: chosen === p, onclick: () => { chosen = p; paint(); } }))); };
      paint();
      const save = Key(t('common.save'), { primary: true, onclick: async () => {
        if (!key.value.trim()) { toast(t('ov.ai.nokey')); return; }
        await api.put(`/api/secrets/${chosen}`, { api_key: key.value.trim() });
        await api.put('/api/settings', { vlm_provider: chosen });
        await refreshSystem(); draw();
      } });
      host.append(h('div', { class: 'ms-caption' }, t('settings.ai.provider')), tags, key.el, h('p', { class: 'data subtle' }, t('settings.ai.note')), h('div', { class: 'row' }, save, edit && Key(t('common.cancel'), { onclick: () => draw() })));
      return;
    }
    const est = await api.get(`/api/projects/${pid}/vlm/estimate?provider=${provider}`);
    const jb = jobBox();
    jb.el.classList.add('hidden');
    const run = Key(t('num.vlm.run'), { primary: est.crops > 0, disabled: est.crops === 0, h: 52, onclick: async () => {
      run.disabled = true; jb.el.classList.remove('hidden');
      try {
        const job = await api.post(`/api/projects/${pid}/vlm/run`);
        jb.stop.onclick = () => api.post(`/api/jobs/${job.id}/cancel`);
        const done = await watchJob(job, jb.tick);
        toast(t('num.vlm.found', { n: done.result?.numbers_found ?? 0 }));
        onDone?.();
      } catch (e) { toast(e.message); run.disabled = false; }
    } });
    host.append(h('div', { class: 'row wrap' }, Tag(AI_NAMES[provider] || provider, { kind: 'cobalt' }), h('span', { class: 'data subtle' }, t('num.vlm.estimate', { n: est.crops, c: est.approx_cost_usd.toFixed(2) })),
      Key(t('ov.ai.change'), { h: 36, onclick: () => draw(true) })),
      h('p', { class: 'data subtle' }, t('num.vlm.body')), h('div', null, run), jb.el);
  }
  await draw();
  return host;
}

// ----------------------------------------------------------------- 5. tagging
function reviewStep(ctx) {
  const { pid, project } = ctx;
  const st = project.stats;
  const big = (n, label, key, tab, primary) => h('div', { class: 'panel raised' }, h('div', { class: 'panel-in stack' },
    h('div', { class: 'ms-caption' }, label), h('div', { class: 'hero-num' }, pad3(n)), Key(key, { primary: primary && n > 0, disabled: n === 0, h: 48, onclick: () => go(`e/${pid}/${tab}`) })));
  const more = h('div', { class: 'row wrap' },
    Key(t('ov.more.examples'), { h: 40, onclick: () => teachMore(ctx) }),
    Key(t('ov.more.ai'), { h: 40, onclick: () => aiDialog(ctx) }),
    Key(t('tab.albums'), { h: 40, onclick: () => go(`e/${pid}/albums`) }),
    Key(t('tab.export'), { h: 40, primary: st.to_tag === 0 && st.photos > 0, onclick: () => go(`e/${pid}/export`) }));
  return h('div', { class: 'stack gap-6' },
    h('p', { class: 'muted' }, t('ov.review.body')),
    h('div', { class: 'cols' }, big(st.to_check_pairs, t('ov.review.check'), t('ov.review.check.go'), 'check', true), big(st.to_tag, t('ov.review.tag'), t('ov.review.tag.go'), 'tag', true)),
    more);
}

function teachMore(ctx) {
  const holder = h('div');
  const m = modal({ title: t('ov.more.examples'), wide: true, body: holder });
  const draw = async () => {
    clear(holder).append(await examplesPanel(ctx, { onChange: draw }));
    const cal = await api.get(`/api/projects/${ctx.pid}/calibration`);
    const bar = DotBar({ n: 48 }); bar.classList.add('fill', 'hidden');
    const apply = Key(t('num.apply'), { primary: !!cal.profile, disabled: !cal.profile, h: 48, onclick: async () => {
      apply.disabled = true; bar.classList.remove('hidden');
      try { const job = await api.post(`/api/projects/${ctx.pid}/calibration/apply`); await watchJob(job, (j) => bar.set(j.total ? j.done / j.total : 0)); toast(t('common.landed')); m.close(); ctx.reload(); }
      catch (e) { toast(e.message); apply.disabled = false; }
    } });
    holder.append(h('div', { class: 'stack', style: { marginTop: '20px' } }, bar, h('div', null, apply)));
  };
  draw();
}

async function aiDialog(ctx) {
  const m = modal({ title: t('ov.ai'), body: await aiPanel(ctx, { onDone: () => { m.close(); ctx.reload(); } }) });
}

// ---------------------------------------------------------- participant names
async function namesCard(ctx) {
  const { pid } = ctx;
  const comps = await api.get(`/api/projects/${pid}/competitors`);
  const area = h('textarea', { class: 'area', placeholder: t('roster.paste.ph'), 'aria-label': t('roster.paste'), spellcheck: 'false', style: { minHeight: '120px' } });
  const add = Key(t('roster.add'), { primary: !comps.length, onclick: async () => {
    if (!area.value.trim()) return;
    const r = await api.post(`/api/projects/${pid}/competitors/import`, { text: area.value });
    toast(`${t('common.landed')} +${r.added}`); ctx.reload();
  } });
  return h('div', { class: 'panel' }, h('div', { class: 'panel-in stack' },
    h('div', { class: 'row' }, h('div', { class: 'title grow' }, t('ov.names')), h('div', { class: 'ms-caption' }, t('roster.count', { n: comps.length }))),
    h('p', { class: 'muted' }, t('ov.names.body')), area, h('p', { class: 'data subtle' }, t('roster.paste.hint')), h('div', null, add)));
}
