// Check: per competitor, a grid of photos that are probably theirs. Click the wrong ones, confirm the rest.
// Also the shared header (progress and the little game) used by the Tag screen.
import { t, pad3 } from './i18n.js';
import { h, api, img, crop, clear, Key, DotBar, Knob, toast, burst, burstAt, bindKeys } from './lib.js';
import { go } from './app.js';

export function reviewHeader(pid, { knob } = {}) {
  const bar = DotBar({ n: 56 }); bar.classList.add('fill');
  const progText = h('div', { class: 'ms-caption' });
  const gameBox = h('div', { class: 'game' });
  let level = null;
  const game = (label, value, hot) => h('div', { class: 'g' }, h('div', { class: 'ms-caption' }, label), h('div', { class: `num${hot ? ' combo-hot' : ''}` }, value));
  const el = h('div', { class: 'rv-head', style: { marginBottom: 0 } }, h('div', { class: 'stack grow', style: { gap: '10px', minWidth: '260px' } }, bar, progText), gameBox, knob && h('div', { title: t('review.finetune.hint') }, knob));
  async function refresh() {
    const [st, g] = await Promise.all([api.get(`/api/projects/${pid}/review/stats`), api.get(`/api/projects/${pid}/review/game`)]);
    bar.set(st.progress);
    progText.textContent = `${t('review.progress', { a: st.resolved_photos, b: st.photos })} · ${t('review.left', { n: st.to_tag })}`;
    clear(gameBox).append(game(t('review.combo'), g.combo > 1 ? `×${g.combo}` : '—', g.combo >= 5), game(t('review.pace'), g.per_minute ? String(Math.round(g.per_minute)) : '—'),
      game(t('review.today'), String(g.today)), game(t('review.level'), String(g.level)));
    if (level !== null && g.level > level) { burst(innerWidth / 2, 160, { n: 28 }); toast(`${t('review.level')} ${g.level}`); }
    level = g.level;
    return st;
  }
  return { el, refresh };
}

const skipped = new Set();

export async function renderCheck(ctx) {
  const { pid, project } = ctx;
  const body = h('div');
  const knob = Knob({ value: Math.round(project.settings.auto_threshold * 100), big: false, label: t('review.finetune'), onchange: async (v) => {
    await api.put(`/api/projects/${pid}/settings`, { auto_threshold: Math.max(0.3, v / 100) });
    project.settings.auto_threshold = Math.max(0.3, v / 100); await head.refresh(); draw();
  } });
  const head = reviewHeader(pid, { knob });
  const root = h('div', { class: 'stack gap-6' }, h('div', { class: 'panel' }, h('div', { class: 'panel-in' }, head.el)), body);

  async function draw() {
    clear(body);
    try { body.append(await gridView(ctx, head, draw)); } catch (e) { body.append(h('p', { class: 'err' }, e.message)); }
  }
  await head.refresh();
  await draw();
  return root;
}

const empty = (ctx) => h('div', { class: 'empty' }, h('div', { class: 'display-l' }, 'ø'), h('p', { class: 'body-l muted' }, t('review.empty')), h('p', { class: 'data subtle' }, t('review.empty.hint')),
  h('div', { class: 'row' }, Key(t('tab.tag'), { primary: true, onclick: () => go(`e/${ctx.pid}/tag`) }), Key(t('tab.export'), { onclick: () => go(`e/${ctx.pid}/export`) })));

async function gridView(ctx, head, redraw) {
  const { pid } = ctx;
  const batches = (await api.get(`/api/projects/${pid}/review/batches?limit=12&per_batch=60`)).filter((x) => !skipped.has(x.competitor.id));
  if (!batches.length) return empty(ctx);
  const b = batches[0], c = b.competitor;
  const wrong = new Set();
  const cells = [];
  const grid = h('div', { class: 'grid big' });
  const counter = h('div', { class: 'ms-caption' });
  const upd = () => { counter.textContent = `${b.items.length - wrong.size} ✓ · ${wrong.size} ✕`; };
  b.items.forEach((it, i) => {
    const src = it.box ? crop(pid, it.photo_id, it.box, 360) : img(pid, it.photo_id, 320);
    const cell = h('button', { class: 'cell', type: 'button', 'aria-pressed': 'false', 'aria-label': `${i + 1}`, onclick: () => toggle(i) },
      h('div', { class: 'ph' }, h('img', { src, alt: '', loading: 'lazy' })), h('div', { class: 'mark' }), h('span', { class: 'pct', title: t('tag.confidence') }, Math.round(it.p * 100)));
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
    if (b.total <= b.items.length) { toast(t('review.complete', { n: c.label })); burst(innerWidth / 2, 220, { n: 30 }); } else toast(t('review.good'));
    await head.refresh(); redraw();
  } });
  const skip = Key(t('common.skip'), { legend: 'S', h: 52, onclick: () => { skipped.add(c.id); redraw(); } });
  const undo = async () => { const r = await api.post(`/api/projects/${pid}/review/undo`); toast(r.restored ? t('common.undo') : '…'); await head.refresh(); redraw(); };
  bindKeys((e) => {
    if (e.target.matches?.('input, textarea, select') || e.metaKey || e.ctrlKey || document.querySelector('.overlay')) return;
    const map = { arrowright: () => move(1), arrowleft: () => move(-1), arrowdown: () => move(cols()), arrowup: () => move(-cols()), x: () => toggle(focus), ' ': () => toggle(focus), enter: () => confirm.click(), s: () => skip.click(), z: undo };
    const f = map[e.key.toLowerCase()];
    if (f) { e.preventDefault(); f(); }
  });
  upd();
  return h('div', null,
    h('div', { class: 'grid-title' }, h('div', { class: 'num' }, c.label), h('div', { class: 'stack grow', style: { gap: '4px' } }, h('div', { class: 'title' }, c.name || t('review.grid.title', { n: c.label })), h('div', { class: 'data subtle' }, t('review.grid.hint')), counter)),
    grid, h('div', { class: 'row', style: { marginTop: '20px' } }, confirm, skip, h('span', { class: 'data subtle' }, b.total > b.items.length ? t('review.grid.more', { n: b.total - b.items.length }) : '')));
}
