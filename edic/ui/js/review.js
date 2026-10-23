// Review: grid, cards, groups, unknown, album — the part that should feel like a game, not paperwork.
import { t, pad3 } from './i18n.js';
import { h, api, img, crop, clear, Key, Tag, Field, DotBar, Knob, toast, modal, burst, burstAt, evidenceChips } from './lib.js';

const MODES = ['grid', 'cards', 'groups', 'unknown', 'album'];
let lastMode = 'grid';

export async function renderReview({ pid, project }) {
  const root = h('div');
  const body = h('div');
  const bar = DotBar({ n: 56 }); bar.classList.add('fill');
  const progText = h('div', { class: 'ms-caption' });
  const gameBox = h('div', { class: 'game' });
  const cleanups = [];
  let comps = await api.get(`/api/projects/${pid}/competitors`);
  let level = null;

  const ctx = {
    pid, project, comps,
    byNumber: (n) => comps.find((c) => c.number === n),
    onCleanup: (fn) => cleanups.push(fn),
    refresh: async () => {
      const [st, g] = await Promise.all([api.get(`/api/projects/${pid}/review/stats`), api.get(`/api/projects/${pid}/review/game`)]);
      bar.set(st.progress);
      progText.textContent = t('review.progress', { a: st.resolved_photos, b: st.photos });
      clear(gameBox).append(
        game(t('review.combo'), g.combo > 1 ? `×${g.combo}` : '—', g.combo >= 5), game(t('review.pace'), g.per_minute ? String(Math.round(g.per_minute)) : '—'),
        game(t('review.today'), String(g.today)), game(t('review.level'), String(g.level)));
      if (level !== null && g.level > level) { burst(innerWidth / 2, 160, { n: 28 }); toast(`${t('review.level')} ${g.level}`); }
      level = g.level;
      return st;
    },
  };
  const game = (label, value, hot) => h('div', { class: 'g' }, h('div', { class: 'ms-caption' }, label), h('div', { class: `num${hot ? ' combo-hot' : ''}` }, value));

  const tabs = h('div', { class: 'tags' });
  const knob = Knob({ value: Math.round(project.settings.auto_threshold * 100), big: false, label: t('review.finetune'), onchange: async (v) => {
    await api.put(`/api/projects/${pid}/settings`, { auto_threshold: Math.max(0.3, v / 100) });
    project.settings.auto_threshold = Math.max(0.3, v / 100); await ctx.refresh(); show(lastMode);
  } });

  async function show(mode) {
    lastMode = mode;
    cleanups.splice(0).forEach((f) => f());
    clear(tabs).append(...MODES.map((m) => Tag(t('review.' + m), { pressed: m === mode, onclick: () => show(m) })));
    clear(body);
    try { body.append(await VIEWS[mode](ctx)); } catch (e) { body.append(h('p', { class: 'err' }, e.message)); }
  }

  root.append(
    h('div', { class: 'panel' }, h('div', { class: 'panel-in stack' },
      h('div', { class: 'rv-head', style: { marginBottom: 0 } },
        h('div', { class: 'stack grow', style: { gap: '10px', minWidth: '260px' } }, tabs, bar, progText),
        gameBox, h('div', { title: t('review.finetune.hint') }, knob)))),
    h('div', { style: { height: '24px' } }), body);
  window.addEventListener('hashchange', () => cleanups.splice(0).forEach((f) => f()), { once: true });
  await ctx.refresh();
  await show(lastMode);
  return root;
}

const empty = (extra) => h('div', { class: 'empty' }, h('div', { class: 'display-l' }, 'ø'), h('p', { class: 'body-l muted' }, t('review.empty')), h('p', { class: 'data subtle' }, t('review.empty.hint')), extra);

/** One keyboard map at a time per review view: binding again replaces the previous one. */
function keys(ctx, map) {
  ctx._off?.();
  const fn = (e) => {
    if (e.target.matches('input, textarea, select') || e.metaKey || e.ctrlKey) return;
    const f = map[e.key.toLowerCase()];
    if (f) { e.preventDefault(); f(); }
  };
  document.addEventListener('keydown', fn);
  ctx._off = () => document.removeEventListener('keydown', fn);
  ctx.onCleanup(ctx._off);
}

async function undo(ctx, after) {
  const r = await api.post(`/api/projects/${ctx.pid}/review/undo`);
  toast(r.restored ? t('common.undo') : '…');
  await ctx.refresh(); after?.();
}

// --------------------------------------------------------------------- grid
async function gridView(ctx) {
  const { pid } = ctx;
  const batches = (await api.get(`/api/projects/${pid}/review/batches?limit=12&per_batch=60`)).filter((x) => !skipped.has(x.competitor.id));
  if (!batches.length) return empty();
  const b = batches[0], c = b.competitor;
  const wrong = new Set();
  const cells = [];
  const grid = h('div', { class: 'grid big' });
  const counter = h('div', { class: 'ms-caption' });
  const upd = () => { counter.textContent = `${b.items.length - wrong.size} ✓ · ${wrong.size} ✕`; };
  b.items.forEach((it, i) => {
    const src = it.box ? crop(pid, it.photo_id, it.box, 360) : img(pid, it.photo_id, 320);
    const cell = h('button', { class: 'cell', type: 'button', 'aria-pressed': 'false', 'aria-label': `${i + 1}`, onclick: () => toggle(i) },
      h('div', { class: 'ph' }, h('img', { src, alt: '', loading: 'lazy' })), h('div', { class: 'mark' }), h('span', { class: 'pct' }, Math.round(it.p * 100)));
    cell.style.aspectRatio = it.box ? '3/5' : '3/4';
    cells.push(cell); grid.append(cell);
  });
  function toggle(i) {
    const it = b.items[i];
    if (wrong.has(it.photo_id)) wrong.delete(it.photo_id); else wrong.add(it.photo_id);
    cells[i].setAttribute('aria-pressed', String(wrong.has(it.photo_id))); upd();
  }
  let focus = 0;
  const cols = () => Math.max(1, Math.floor(grid.clientWidth / (cells[0].offsetWidth + 12)));
  const move = (d) => { focus = Math.max(0, Math.min(cells.length - 1, focus + d)); cells[focus].focus(); };
  const confirm = Key(t('review.grid.confirm'), { primary: true, legend: '⏎', h: 52, onclick: async () => {
    const accepted = b.items.filter((x) => !wrong.has(x.photo_id)).map((x) => x.photo_id);
    await api.post(`/api/projects/${pid}/review/batch`, { competitor_id: c.id, accepted, rejected: [...wrong] });
    burstAt(confirm, { n: 18 });
    if (b.total <= b.items.length) { toast(t('review.complete', { n: pad3(c.number) })); burst(innerWidth / 2, 220, { n: 30 }); } else toast(t('review.good'));
    await ctx.refresh(); swap();
  } });
  const skip = Key(t('common.skip'), { legend: 'S', h: 52, onclick: () => { skipped.add(c.id); swap(); } });
  const host = h('div');
  const swap = async () => { const p = host.parentElement; const nv = await gridView(ctx); p.replaceChild(nv, host); };
  keys(ctx, { arrowright: () => move(1), arrowleft: () => move(-1), arrowdown: () => move(cols()), arrowup: () => move(-cols()), x: () => toggle(focus), ' ': () => toggle(focus), enter: () => confirm.click(), s: () => skip.click(), z: () => undo(ctx, swap) });
  upd();
  host.append(
    h('div', { class: 'grid-title' }, h('div', { class: 'num' }, pad3(c.number)), h('div', { class: 'stack grow', style: { gap: '4px' } }, h('div', { class: 'title' }, c.name || t('review.grid.title', { n: pad3(c.number) })), h('div', { class: 'data subtle' }, t('review.grid.hint')), counter)),
    grid, h('div', { class: 'row', style: { marginTop: '20px' } }, confirm, skip, h('span', { class: 'data subtle' }, b.total > b.items.length ? t('review.grid.more', { n: b.total - b.items.length }) : '')));
  return host;
}
const skipped = new Set();

// -------------------------------------------------------------------- cards
async function cardsView(ctx) {
  const { pid } = ctx;
  let queue = await api.get(`/api/projects/${pid}/review/cards?limit=40`);
  if (!queue.length) return empty();
  const host = h('div');
  const draw = () => {
    clear(host);
    const it = queue[0];
    if (!it) return refill();
    const c = it.competitor;
    const photo = h('div', { class: 'photo-wrap card-anim' }, h('img', { src: img(pid, it.photo_id, 1280), alt: '' }));
    if (it.box && it.width) {
      const [x1, y1, x2, y2] = it.box;
      photo.append(h('div', { class: 'pbox', style: { left: x1 / it.width * 100 + '%', top: y1 / it.height * 100 + '%', width: (x2 - x1) / it.width * 100 + '%', height: (y2 - y1) / it.height * 100 + '%' } }, h('span', null, pad3(c.number))));
    }
    const decide = async (yes) => {
      await api.post(`/api/projects/${pid}/review/${yes ? 'accept' : 'reject'}`, { photo_id: it.photo_id, competitor_id: c.id, person_idx: it.person_idx });
      if (yes) burstAt(yesKey, { n: 10 });
      queue.shift(); await ctx.refresh(); draw();
    };
    const yesKey = Key(t('review.yes'), { primary: true, legend: '→ Y', h: 56, onclick: () => decide(true) });
    const noKey = Key(t('review.no'), { legend: '← N', h: 56, onclick: () => decide(false) });
    keys(ctx, { arrowright: () => decide(true), y: () => decide(true), arrowleft: () => decide(false), n: () => decide(false), ' ': () => { queue.push(queue.shift()); draw(); }, z: () => undo(ctx, refill) });
    if (queue[1]) new Image().src = img(pid, queue[1].photo_id, 1280);
    host.append(h('div', { class: 'cards-stage' }, photo,
      h('div', { class: 'stack gap-6' },
        h('div', { class: 'panel raised' }, h('div', { class: 'panel-in stack' },
          h('div', { class: 'ms-caption' }, `${Math.round(it.p * 100)} %`), h('div', { class: 'big-q' }, t('review.cards.q', { n: pad3(c.number) })), c.name && h('div', { class: 'title muted' }, c.name),
          h('div', { class: 'tags' }, evidenceChips(it.evidence)),
          it.box && h('div', { class: 'ph', style: { width: '140px', height: '230px' } }, h('img', { src: crop(pid, it.photo_id, it.box, 320), alt: '' })))),
        h('div', { class: 'row' }, yesKey, noKey),
        h('div', { class: 'row' }, Key(t('common.skip'), { legend: '␣', h: 40, onclick: () => { queue.push(queue.shift()); draw(); } }), Key(t('common.undo'), { legend: 'Z', h: 40, onclick: () => undo(ctx, refill) })))));
  };
  async function refill() { queue = await api.get(`/api/projects/${pid}/review/cards?limit=40`); if (!queue.length) { clear(host).append(empty()); return; } draw(); }
  draw();
  return host;
}

// ------------------------------------------------------------------- groups
async function groupsView(ctx) {
  const { pid } = ctx;
  const items = await api.get(`/api/projects/${pid}/review/tags?limit=12`);
  if (!items.length) return empty();
  const host = h('div');
  let idx = 0;
  const draw = () => {
    clear(host);
    const d = items[idx];
    if (!d) return host.append(empty());
    // person index -> competitor id, pre-filled from the model's suggestions
    const map = new Map();
    for (const c of [...d.candidates].sort((a, b) => a.p - b.p)) if (c.person_idx != null && c.status !== 'rejected') map.set(c.person_idx, c.competitor_id);
    let sel = d.persons.length ? d.persons.reduce((a, p) => ((p.box[3] - p.box[1]) > (a.box[3] - a.box[1]) ? p : a)).idx : null;
    const boxes = new Map();
    const photo = h('div', { class: 'photo-wrap card-anim' }, h('img', { src: img(pid, d.id, 1280), alt: '' }));
    const label = (p) => { const c = ctx.comps.find((x) => x.id === map.get(p.idx)); return c ? pad3(c.number) : '?'; };
    const paint = () => boxes.forEach((el, i) => { el.classList.toggle('sel', i === sel); el.classList.toggle('dim', !map.has(i)); el.firstChild.textContent = label(d.persons[i]); });
    for (const p of d.persons) {
      const [x1, y1, x2, y2] = p.box;
      if ((y2 - y1) < d.height * 0.12) continue;           // audience and far-away people stay out of the way
      const el = h('button', { class: 'pbox', type: 'button', style: { left: x1 / d.width * 100 + '%', top: y1 / d.height * 100 + '%', width: (x2 - x1) / d.width * 100 + '%', height: (y2 - y1) / d.height * 100 + '%' }, onclick: () => { sel = p.idx; paint(); pickerFor(); input.input.focus(); } }, h('span'));
      boxes.set(p.idx, el); photo.append(el);
    }
    const pickBox = h('div', { class: 'pick' });
    const input = Field({ placeholder: '041', id: 'who', maxlength: 4, inputmode: 'numeric', onEnter: (v) => { const c = ctx.byNumber(parseInt(v, 10)); if (!c) return toast(t('review.unknown.wrong', { n: v })); assign(c.id); input.value = ''; } });
    const assign = (cid) => { if (sel == null) return; if (map.get(sel) === cid) map.delete(sel); else map.set(sel, cid); paint(); pickerFor(); };
    function pickerFor() {
      clear(pickBox);
      const cands = [...d.candidates].sort((a, b) => b.p - a.p).filter((c) => c.competitor);
      for (const c of cands) pickBox.append(Tag(`${pad3(c.competitor.number)} ${Math.round(c.p * 100)}`, { pressed: map.get(sel) === c.competitor_id, onclick: () => assign(c.competitor_id) }));
    }
    const save = Key(t('review.groups.save'), { primary: true, legend: '⏎', h: 52, onclick: async () => {
      const person_map = {}; const ids = new Set();
      map.forEach((cid, pi) => { ids.add(cid); person_map[cid] = pi; });
      await api.post(`/api/projects/${pid}/review/photo`, { photo_id: d.id, competitor_ids: [...ids], person_map });
      burstAt(save, { n: 14 }); idx++; await ctx.refresh(); draw();
    } });
    keys(ctx, { enter: () => save.click(), ' ': () => { idx++; draw(); }, z: () => undo(ctx, () => { idx = Math.max(0, idx - 1); draw(); }) });
    host.append(h('div', { class: 'cards-stage' }, photo,
      h('div', { class: 'panel raised' }, h('div', { class: 'panel-in stack' },
        h('div', { class: 'ms-caption' }, t('review.groups.hint')), h('div', { class: 'title' }, t('review.groups.who')), pickBox, input.el,
        h('div', { class: 'row' }, save, Key(t('common.skip'), { h: 40, legend: '␣', onclick: () => { idx++; draw(); } }))))));
    paint(); pickerFor();
  };
  draw();
  return host;
}

// ------------------------------------------------------------------ unknown
async function unknownView(ctx) {
  const { pid } = ctx;
  const items = await api.get(`/api/projects/${pid}/review/unknown?limit=40`);
  if (!items.length) return empty();
  let idx = 0;
  const host = h('div');
  const draw = () => {
    clear(host);
    const it = items[idx];
    if (!it) return host.append(empty());
    const photo = h('div', { class: 'photo-wrap card-anim' }, h('img', { src: img(pid, it.id, 1280), alt: '' }));
    const input = Field({ placeholder: t('review.unknown.ph'), id: 'nums', maxlength: 24, autofocus: true, onEnter: submit });
    async function submit(v) {
      v = v.trim().toLowerCase();
      if (!v) return;
      if (v === 'n') { await api.post(`/api/projects/${pid}/review/nobody`, { photo_id: it.id }); return next(); }
      const nums = v.split(/[\s,;]+/).map((x) => parseInt(x, 10)).filter((x) => !Number.isNaN(x));
      const found = nums.map((n) => ctx.byNumber(n));
      const bad = nums.filter((n, i) => !found[i]);
      if (!nums.length || bad.length) return toast(t('review.unknown.wrong', { n: bad.join(' ') || v }));
      await api.post(`/api/projects/${pid}/review/photo`, { photo_id: it.id, competitor_ids: found.map((c) => c.id) });
      burstAt(input.input, { n: 12 }); next();
    }
    const next = async () => { idx++; await ctx.refresh(); draw(); };
    keys(ctx, { z: () => undo(ctx, () => { idx = Math.max(0, idx - 1); draw(); }) });
    host.append(h('div', { class: 'unknown-stage' }, photo,
      h('div', { class: 'panel raised' }, h('div', { class: 'panel-in stack' },
        h('div', { class: 'ms-caption' }, t('review.unknown.hint')), input.el,
        h('div', { class: 'row' }, Key(t('review.unknown.nobody'), { legend: 'N', h: 44, onclick: () => submit('n') }), Key(t('common.skip'), { h: 44, onclick: () => { idx++; draw(); } }), Key(t('common.undo'), { legend: 'Z', h: 44, onclick: () => undo(ctx, () => { idx = Math.max(0, idx - 1); draw(); }) })),
        h('div', { class: 'ms-caption' }, `${idx + 1} / ${items.length}`)))));
    if (items[idx + 1]) new Image().src = img(pid, items[idx + 1].id, 1280);
  };
  draw();
  return host;
}

// -------------------------------------------------------------------- album
async function albumView(ctx) {
  const { pid } = ctx;
  const album = await api.get(`/api/projects/${pid}/review/album`);
  if (!album.length) return empty();
  const cards = album.map((c) => {
    const f = c.confirmed + c.auto + c.check ? (c.confirmed + c.auto) / (c.confirmed + c.auto + c.check) : 0;
    const bar = DotBar({ value: f, n: 20 }); bar.classList.add('mini');
    return h('button', { class: 'acard', type: 'button', onclick: () => openCompetitor(ctx, c) },
      h('div', { class: 'ph cover', style: { position: 'relative', aspectRatio: '4/5' } }, c.cover_photo ? h('img', { src: img(pid, c.cover_photo, 320), alt: '', loading: 'lazy' }) : h('div', { class: 'empty', style: { padding: '40px 0' } }, h('span', { class: 'ms-caption' }, t('album.none'))), c.complete && h('span', { class: 'oslash' })),
      h('div', { class: 'meta' }, h('div', { class: 'big' }, pad3(c.number)), c.name && h('div', { class: 'title muted', style: { fontSize: '20px' } }, c.name), bar,
        h('div', { class: 'data subtle' }, `${c.confirmed} ${t('album.confirmed')} · ${c.auto} ${t('album.auto')}${c.check ? ` · ${c.check} ${t('album.check')}` : ''}`)));
  });
  return h('div', { class: 'album' }, cards);
}

async function openCompetitor(ctx, c) {
  const { pid } = ctx;
  const items = await api.get(`/api/projects/${pid}/review/competitor/${c.id}`);
  const grid = h('div', { class: 'grid' });
  for (const it of items) {
    const cell = h('button', { class: 'cell', type: 'button', 'aria-pressed': 'false', title: t('album.remove'), onclick: async () => {
      await api.post(`/api/projects/${pid}/review/reject`, { photo_id: it.photo_id, competitor_id: c.id });
      cell.setAttribute('aria-pressed', 'true'); ctx.refresh();
    } }, h('div', { class: 'ph' }, h('img', { src: it.box ? crop(pid, it.photo_id, it.box, 360) : img(pid, it.photo_id, 320), alt: '', loading: 'lazy' })), h('div', { class: 'mark' }),
    h('span', { class: 'pct' }, it.status === 'accepted' ? '✓' : Math.round(it.p * 100)));
    cell.style.aspectRatio = it.box ? '3/5' : '3/4';
    grid.append(cell);
  }
  modal({ title: `${pad3(c.number)} ${c.name || ''}`, wide: true, body: h('div', { class: 'stack' }, h('p', { class: 'data subtle' }, t('album.remove')), items.length ? grid : h('p', { class: 'muted' }, t('album.none'))) });
}

const VIEWS = { grid: gridView, cards: cardsView, groups: groupsView, unknown: unknownView, album: albumView };
