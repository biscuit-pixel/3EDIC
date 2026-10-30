// Tag: the photos nobody could place. One photo at a time (with its neighbours in view) or a grid for tagging many at once.
//   ← back   → next   ↑ yes   ↓ no   digits + Enter = this person is that number   - = not a competitor   Tab = next person
import { t, pad3 } from './i18n.js';
import { h, api, img, clear, Key, Tag, Field, PhotoStage, placeBox, placePoint, lightbox, previewWidth, bindKeys, toast, burst, burstAt } from './lib.js';
import { go } from './app.js';
import { reviewHeader } from './review.js';

let mode = 'single';
const pct = (p) => `${Math.round(p * 100)}%`;

export async function renderTag(ctx) {
  const head = reviewHeader(ctx.pid);
  const body = h('div');
  const modes = h('div', { class: 'tags' });
  const show = async (m) => {
    mode = m;
    clear(modes).append(...['single', 'grid'].map((k) => Tag(t('tag.mode.' + k), { pressed: k === m, onclick: () => show(k) })));
    clear(body);
    try { body.append(await (m === 'grid' ? bulkView(ctx, head) : singleView(ctx, head))); } catch (e) { body.append(h('p', { class: 'err' }, e.message)); }
  };
  const root = h('div', { class: 'stack', style: { gap: '14px' } }, h('div', { class: 'tag-head' }, modes, head.el), body);
  await head.refresh();
  await show(mode);
  return root;
}

function done(ctx, left) {
  return h('div', { class: 'empty' }, h('div', { class: 'display-l' }, 'ø'), h('p', { class: 'body-l muted' }, left ? t('tag.end', { n: left }) : t('tag.empty')),
    h('div', { class: 'row' }, Key(t('tab.albums'), { primary: !left, onclick: () => go(`e/${ctx.pid}/albums`) }), Key(t('tab.export'), { onclick: () => go(`e/${ctx.pid}/export`) })));
}

// ------------------------------------------------------------------- single
async function singleView(ctx, head) {
  const { pid, project } = ctx;
  const auto = project.settings.auto_threshold;
  const host = h('div');
  let queue = [], reason = new Map(), history = [];
  let curId = null, detail = null, frames = [], sel = null, input = null, busy = false;

  const loadQueue = async () => {
    const q = await api.get(`/api/projects/${pid}/review/queue`);
    queue = q.items.map((i) => i.id); reason = new Map(q.items.map((i) => [i.id, i.reason]));
  };
  await loadQueue();
  if (!queue.length) return done(ctx, 0);

  const significant = (p) => p.box[3] - p.box[1] >= detail.height * 0.12;
  const resolved = (p) => p.assigned.length > 0 || p.suggested.some((s) => s.p >= auto);
  const bySize = (a, b) => (b.box[3] - b.box[1]) - (a.box[3] - a.box[1]);
  const firstOpen = (except = -1) => detail.persons.filter((p) => significant(p) && !resolved(p) && p.idx !== except).sort(bySize)[0];
  const pickPerson = () => {
    const p = firstOpen() || detail.persons.filter(significant).sort(bySize)[0] || [...detail.persons].sort(bySize)[0];
    return p ? p.idx : null;
  };
  const nextQueued = (from) => {
    const i = queue.indexOf(from);
    if (i >= 0) return queue[i + 1] ?? null;
    const at = frames.findIndex((f) => f.id === from);                       // jumped to a photo that is not in the list: continue after it
    for (const f of frames.slice(at + 1)) if (queue.includes(f.id)) return f.id;
    return queue[0] ?? null;
  };

  async function load(id) {
    curId = id;
    [detail, frames] = await Promise.all([api.get(`/api/projects/${pid}/photos/${id}`), api.get(`/api/projects/${pid}/review/context/${id}`).then((r) => r.items)]);
  }
  async function show(id) {
    if (id == null) { clear(host).append(done(ctx, queue.length)); return; }
    await load(id);
    sel = pickPerson();
    draw();
    const nxt = nextQueued(id);
    if (nxt != null) new Image().src = img(pid, nxt, previewWidth());
  }
  const move = async (to, tagged) => {
    if (tagged) queue = queue.filter((x) => x !== curId);
    if (curId != null) history.push(curId);
    await show(to);
  };
  const forward = () => move(nextQueued(curId), false);
  const back = async () => { const p = history.pop(); if (p != null) await show(p); };

  async function tagged() {                                      // after a person was tagged: the next person on this photo, or the next photo
    await head.refresh();
    await load(curId);
    const nxtPerson = detail.persons.length ? firstOpen(sel) : null;
    if (nxtPerson) { sel = nxtPerson.idx; draw(); return; }
    await move(nextQueued(curId), true);
  }

  async function act(fn) {
    if (busy) return;
    busy = true;
    try { await fn(); } catch (e) { toast(e.message); } finally { busy = false; }
  }
  const tagNumber = (n) => act(async () => {
    if (!(n >= 1 && n <= 9999)) { toast(t('tag.badnumber')); return; }
    if (detail.persons.length) await api.post(`/api/projects/${pid}/review/person`, { photo_id: curId, person_idx: sel, number: n });
    else await api.post(`/api/projects/${pid}/review/bulk-tag`, { photo_ids: [curId], number: n });
    burst(innerWidth / 2, innerHeight / 2, { n: 10 });
    await tagged();
  });
  const tagOthers = () => act(async () => {
    if (detail.persons.length) await api.post(`/api/projects/${pid}/review/person`, { photo_id: curId, person_idx: sel, others: true });
    else await api.post(`/api/projects/${pid}/review/bulk-tag`, { photo_ids: [curId], others: true });
    await tagged();
  });
  const person = () => detail.persons.find((p) => p.idx === sel);
  const topSuggestion = () => [...(person()?.suggested || [])].sort((a, b) => b.p - a.p)[0];
  const accept = (s) => act(async () => {
    const c = s.competitor;
    await api.post(`/api/projects/${pid}/review/person`, { photo_id: curId, person_idx: sel, competitor_id: c.id, number: c.number || 1, others: c.kind === 'others' });
    await tagged();
  });
  const yes = () => { const s = topSuggestion(); if (s) accept(s); else toast(t('tag.nosuggestion')); };
  const no = () => act(async () => {
    if (!topSuggestion()) { toast(t('tag.nosuggestion')); return; }
    await api.post(`/api/projects/${pid}/review/person/reject`, { photo_id: curId, person_idx: sel });
    await load(curId); draw();
  });
  const undo = () => act(async () => {
    const r = await api.post(`/api/projects/${pid}/review/undo`);
    if (!r.restored) { toast(t('tag.nothing.undo')); return; }
    toast(t('common.undo'));
    await loadQueue(); await head.refresh();
    await show(r.restored[0]);
  });
  const zoom = () => lightbox(img(pid, curId, 2400));
  const cycle = (d) => {
    const ids = detail.persons.map((p) => p.idx);
    if (ids.length > 1) { sel = ids[(ids.indexOf(sel) + d + ids.length) % ids.length]; draw(); }
  };

  function draw() {
    clear(host);
    const stage = PhotoStage({ src: img(pid, curId, previewWidth()), width: detail.width, height: detail.height, maxVh: 48, cls: 'card-anim' });
    const labelOf = (p) => {
      if (p.assigned.length) return { text: p.assigned.map((c) => c.label).join(' '), kind: 'done' };
      const s = [...p.suggested].sort((a, b) => b.p - a.p)[0];
      return s ? { text: `${s.competitor.label}?`, kind: 'sug', conf: s.p } : { text: '?', kind: 'none' };
    };
    for (const p of detail.persons) {
      const far = !significant(p);
      const box = h('button', { class: `pbox${p.idx === sel ? ' sel' : ''}${far ? ' far' : ''}${resolved(p) ? ' ok' : ''}`, type: 'button', 'aria-label': `${p.idx + 1}`, onclick: () => { sel = p.idx; draw(); } });
      placeBox(box, p.box, detail.width, detail.height);
      const l = labelOf(p);
      const at = p.anchor || [p.box[0], p.box[1]];                      // the label sits where the number is on the shirt
      const chip = h('div', { class: `plabel ${l.kind}${p.idx === sel ? ' sel' : ''}${far ? ' far' : ''}`, onclick: () => { sel = p.idx; draw(); } }, l.text, l.conf != null && h('small', null, pct(l.conf)));
      placePoint(chip, at, detail.width, detail.height);
      stage.layer.append(box, chip);
    }
    const strip = h('div', { class: 'filmstrip', role: 'list' }, frames.map((f) => h('button', { class: `frame ${f.state}${f.current ? ' cur' : ''}`, type: 'button', role: 'listitem', title: f.tags.map((x) => x.label).join(' '),
      onclick: () => { if (!f.current) { history.push(curId); show(f.id); } } },
      h('div', { class: 'ph' }, h('img', { src: img(pid, f.id, 320), alt: '', loading: 'lazy' })),
      h('div', { class: 'ftags' }, f.tags.length ? f.tags.map((x) => h('span', { class: x.status }, x.label)) : h('span', { class: 'none' }, '·')))));

    const p = person();
    const sug = [...(p?.suggested || [])].sort((a, b) => b.p - a.p).slice(0, 4);
    const inQueue = queue.indexOf(curId);
    input = Field({ placeholder: '041', id: 'whoNo', maxlength: 4, inputmode: 'numeric', autofocus: true,
      onEnter: (v) => { v = v.trim(); if (!v) { if (topSuggestion()) yes(); return; } tagNumber(parseInt(v, 10)); } });
    input.input.addEventListener('keydown', (e) => { if (e.key === '-') { e.preventDefault(); tagOthers(); } });
    input.input.addEventListener('input', () => { input.input.value = input.input.value.replace(/\D/g, ''); });
    const yesKey = Key(t('review.yes'), { primary: !!sug.length, disabled: !sug.length, legend: '↑', h: 44, onclick: yes });
    host.append(h('div', { class: 'tag-stage' },
      h('div', { class: 'stack' }, stage.el, strip),
      h('div', { class: 'panel raised' }, h('div', { class: 'panel-in stack' },
        h('div', { class: 'row' }, h('div', { class: 'ms-caption grow' }, inQueue >= 0 ? t('tag.pos', { a: inQueue + 1, b: queue.length }) : t('tag.outside')),
          reason.get(curId) && Tag(t('tag.reason.' + reason.get(curId)), { kind: reason.get(curId) === 'check' ? 'amber' : 'muted' })),
        detail.persons.length > 1 && h('div', { class: 'stack', style: { gap: '8px' } }, h('div', { class: 'ms-caption' }, t('tag.who', { a: detail.persons.findIndex((q) => q.idx === sel) + 1, b: detail.persons.length })),
          h('div', { class: 'tags' }, detail.persons.filter(significant).map((q, i) => Tag(`${i + 1} · ${labelOf(q).text}`, { pressed: q.idx === sel, kind: resolved(q) ? 'cobalt' : '', onclick: () => { sel = q.idx; draw(); } })))),
        sug.length > 0 && h('div', { class: 'stack', style: { gap: '8px' } }, h('div', { class: 'ms-caption' }, t('tag.confidence')),
          h('div', { class: 'tags' }, sug.map((s) => Tag(`${s.competitor.label} · ${pct(s.p)}`, { onclick: () => accept(s), title: t('tag.confidence') })))),
        h('div', { class: 'title' }, t('tag.q')), input.el,
        h('div', { class: 'row wrap' }, yesKey, Key(t('review.no'), { disabled: !sug.length, legend: '↓', h: 44, onclick: no }), Key(t('tag.others'), { legend: '-', h: 44, onclick: tagOthers })),
        h('div', { class: 'row wrap' }, Key(t('common.back'), { legend: '<', h: 36, disabled: !history.length, onclick: back }), Key(t('common.next'), { legend: '>', h: 36, onclick: forward }),
          Key(t('common.undo'), { legend: 'Ctrl Z', h: 36, onclick: undo }), Key(t('tag.zoom'), { legend: '+', h: 36, onclick: zoom })),
        h('p', { class: 'data subtle' }, t('tag.keys'))))));
    requestAnimationFrame(() => input.input.focus());
  }

  bindKeys((e) => {
    if (document.querySelector('.overlay, .lightbox')) return;
    if (e.ctrlKey || e.metaKey) { if (e.key.toLowerCase() === 'z') { e.preventDefault(); undo(); } return; }
    if (!detail || (e.target.matches?.('input, textarea, select') && e.target !== input?.input)) return;
    const map = { ArrowLeft: back, ArrowRight: forward, ArrowUp: yes, ArrowDown: no, '-': tagOthers, '+': zoom, '=': zoom, Tab: () => cycle(e.shiftKey ? -1 : 1) };
    if (map[e.key]) { e.preventDefault(); map[e.key](); return; }
    if (/^\d$/.test(e.key) && input && e.target !== input.input) input.input.focus();
  });

  host.append(h('p', { class: 'muted' }, '…'));
  await show(queue[0]);
  return host;
}

// --------------------------------------------------------------------- grid
async function bulkView(ctx, head) {
  const { pid } = ctx;
  const q = await api.get(`/api/projects/${pid}/review/queue`);
  let items = q.items, shown = 90;
  if (!items.length) return done(ctx, 0);
  const selected = new Set();
  let last = null;
  const grid = h('div', { class: 'grid big' });
  const count = h('div', { class: 'ms-caption', style: { color: 'var(--text)' } });
  const more = Key(t('tag.grid.more'), { h: 40, onclick: () => { shown += 90; draw(); } });

  function draw() {
    clear(grid);
    items.slice(0, shown).forEach((it, i) => {
      const cell = h('button', { class: `cell${selected.has(it.id) ? ' sel' : ''}`, type: 'button', 'aria-pressed': 'false',
        onclick: (e) => {
          if (e.shiftKey && last != null) {
            const a = items.findIndex((x) => x.id === last), b = i;
            for (const x of items.slice(Math.min(a, b), Math.max(a, b) + 1)) selected.add(x.id);
          } else if (selected.has(it.id)) selected.delete(it.id); else selected.add(it.id);
          last = it.id; draw();
        } },
        h('div', { class: 'ph' }, h('img', { src: img(pid, it.id, 320), alt: '', loading: 'lazy' })),
        h('span', { class: `pct ${it.reason}` }, it.reason === 'check' ? '?' : '·'), selected.has(it.id) && h('span', { class: 'tick' }, '✓'));
      grid.append(cell);
    });
    more.classList.toggle('hidden', shown >= items.length);
    count.textContent = t('tag.grid.selected', { n: selected.size, m: items.length });
  }
  async function tagSelected(body) {
    if (!selected.size) { toast(t('tag.grid.none')); return; }
    const ids = [...selected];
    try {
      await api.post(`/api/projects/${pid}/review/bulk-tag`, { photo_ids: ids, ...body });
      items = items.filter((x) => !selected.has(x.id)); selected.clear();
      burstAt(num.input, { n: 14 }); num.value = '';
      await head.refresh();
      if (!items.length) { clear(host).append(done(ctx, 0)); return; }
      draw();
    } catch (e) { toast(e.message); }
  }
  const num = Field({ label: t('tag.grid.number'), placeholder: '041', id: 'bulkNo', maxlength: 4, inputmode: 'numeric', autofocus: true,
    onEnter: (v) => { const n = parseInt(v, 10); if (n >= 1 && n <= 9999) tagSelected({ number: n }); else toast(t('tag.badnumber')); } });
  num.input.addEventListener('keydown', (e) => { if (e.key === '-') { e.preventDefault(); tagSelected({ others: true }); } });
  num.input.addEventListener('input', () => { num.input.value = num.input.value.replace(/\D/g, ''); });
  bindKeys((e) => { if (e.key === 'Escape') { selected.clear(); draw(); } });
  const host = h('div', { class: 'stack gap-6' },
    h('div', { class: 'panel raised' }, h('div', { class: 'panel-in stack' },
      h('p', { class: 'muted' }, t('tag.grid.hint')),
      h('div', { class: 'row wrap top-a' }, h('div', { style: { width: '180px' } }, num.el),
        Key(t('tag.others'), { legend: '-', h: 48, onclick: () => tagSelected({ others: true }) }),
        Key(t('find.select'), { h: 48, onclick: () => { items.slice(0, shown).forEach((x) => selected.add(x.id)); draw(); } }),
        Key(t('tag.grid.clear'), { h: 48, legend: 'Esc', onclick: () => { selected.clear(); draw(); } }), count))),
    grid, h('div', null, more));
  draw();
  return host;
}
