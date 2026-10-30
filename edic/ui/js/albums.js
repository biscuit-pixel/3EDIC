// Albums: one per number. Rename or renumber, merge, split off a look-alike number (56_2), take wrong photos out.
import { t, pad3 } from './i18n.js';
import { h, api, img, crop, clear, Key, Tag, Field, DotBar, lightbox, toast, modal, burstAt } from './lib.js';
import { go } from './app.js';

/** How a photo with several competitors on it is written to the folders. Saved per event. */
export function groupModePicker(ctx, onchange) {
  const box = h('div', { class: 'cols' });
  const draw = () => {
    clear(box).append(...['all', 'main', 'separate', 'both'].map((m) => h('button', { class: 'choice', type: 'button', 'aria-pressed': String(ctx.project.settings.group_mode === m), onclick: async () => {
      await api.put(`/api/projects/${ctx.pid}/settings`, { group_mode: m }); ctx.project.settings.group_mode = m; draw(); onchange?.(m);
    } }, h('span', { class: 't' }, t('group.' + m)), h('span', { class: 'd' }, t('group.' + m + '.d')))));
  };
  draw();
  return h('div', { class: 'stack' }, h('div', { class: 'ms-caption' }, t('group.title')), box, h('p', { class: 'data subtle' }, t('group.note')));
}

export async function renderAlbums(ctx) {
  const { pid } = ctx;
  const album = await api.get(`/api/projects/${pid}/review/album`);
  const comps = await api.get(`/api/projects/${pid}/competitors`);
  const empty = comps.filter((c) => !album.find((a) => a.id === c.id)?.photos);
  const addNumber = Field({ placeholder: '041', id: 'newAlbum', maxlength: 4, inputmode: 'numeric', onEnter: () => addBtn.click() });
  const addBtn = Key(t('albums.add'), { h: 40, onclick: async () => {
    const n = parseInt(addNumber.value, 10);
    if (!(n >= 1 && n <= 9999)) { toast(t('tag.badnumber')); return; }
    await api.post(`/api/projects/${pid}/competitors`, { number: n }); ctx.reload();
  } });
  const bar = h('div', { class: 'panel' }, h('div', { class: 'panel-in stack' },
    h('p', { class: 'muted' }, t('albums.body')),
    h('div', { class: 'row wrap top-a' }, h('div', { style: { width: '140px' } }, addNumber.el), addBtn,
      Key(t('albums.names'), { h: 40, onclick: () => namesDialog(ctx) }),
      Key(t('albums.delempty', { n: empty.length }), { h: 40, disabled: !empty.length, onclick: async () => {
        const r = await api.del(`/api/projects/${pid}/albums/empty`); toast(t('albums.deleted', { n: r.deleted })); ctx.reload();
      } }))));
  const groups = h('div', { class: 'panel' }, h('div', { class: 'panel-in' }, groupModePicker(ctx)));
  if (!album.length) return h('div', { class: 'stack gap-6' }, bar, groups, h('div', { class: 'empty' }, h('p', { class: 'body-l muted' }, t('albums.none'))));
  const cards = album.map((c) => {
    const f = c.confirmed + c.auto + c.check ? (c.confirmed + c.auto) / (c.confirmed + c.auto + c.check) : 0;
    const bar_ = DotBar({ value: f, n: 20 }); bar_.classList.add('mini');
    return h('button', { class: 'acard', type: 'button', onclick: () => openAlbum(ctx, c) },
      h('div', { class: 'ph cover', style: { position: 'relative', aspectRatio: '4/5' } },
        c.cover_photo ? h('img', { src: img(pid, c.cover_photo, 320), alt: '', loading: 'lazy' }) : h('div', { class: 'empty', style: { padding: '40px 0' } }, h('span', { class: 'ms-caption' }, t('album.none'))),
        c.complete && h('span', { class: 'oslash' })),
      h('div', { class: 'meta' }, h('div', { class: 'big' }, c.kind === 'others' ? t('album.others') : c.label), c.name && c.kind !== 'others' && h('div', { class: 'title muted', style: { fontSize: '20px' } }, c.name), bar_,
        h('div', { class: 'data subtle' }, `${c.confirmed} ${t('album.confirmed')} · ${c.auto} ${t('album.auto')}${c.check ? ` · ${c.check} ${t('album.check')}` : ''}`)));
  });
  return h('div', { class: 'stack gap-6' }, bar, groups, h('div', { class: 'album' }, cards));
}

function namesDialog(ctx) {
  const area = h('textarea', { class: 'area', placeholder: t('roster.paste.ph'), spellcheck: 'false', style: { minHeight: '160px' } });
  const m = modal({ title: t('albums.names'), body: h('div', { class: 'stack' }, h('p', { class: 'muted' }, t('albums.names.body')), area, h('p', { class: 'data subtle' }, t('roster.paste.hint'))),
    actions: [Key(t('common.cancel'), { onclick: () => m.close() }), Key(t('roster.add'), { primary: true, onclick: async () => {
      if (!area.value.trim()) return;
      const r = await api.post(`/api/projects/${ctx.pid}/competitors/import`, { text: area.value }); toast(`${t('common.landed')} +${r.added}`); m.close(); ctx.reload();
    } })] });
}

async function openAlbum(ctx, c) {
  const { pid } = ctx;
  const others = c.kind === 'others';
  const items = await api.get(`/api/projects/${pid}/review/competitor/${c.id}`);
  const selected = new Set();
  const grid = h('div', { class: 'grid' });
  const count = h('div', { class: 'ms-caption', style: { color: 'var(--text)' } });
  const actionsRow = h('div', { class: 'row wrap' });
  const refreshActions = () => {
    count.textContent = t('tag.grid.selected', { n: selected.size, m: items.length });
    clear(actionsRow).append(...[
      Key(t('album.remove.sel'), { h: 40, disabled: !selected.size, onclick: () => removeDialog() }),
      !others && Key(t('album.split'), { h: 40, disabled: !selected.size, title: t('album.split.d'), onclick: split }),
      Key(t('album.preview'), { h: 40, disabled: selected.size !== 1, onclick: () => lightbox(img(pid, [...selected][0], 2400)) }),
      Key(t('find.select'), { h: 40, onclick: () => { items.forEach((x) => selected.add(x.photo_id)); drawGrid(); } })].filter(Boolean));
  };
  function drawGrid() {
    clear(grid);
    for (const it of items) {
      const cell = h('button', { class: `cell${selected.has(it.photo_id) ? ' sel' : ''}`, type: 'button', 'aria-pressed': 'false', onclick: () => { selected.has(it.photo_id) ? selected.delete(it.photo_id) : selected.add(it.photo_id); drawGrid(); } },
        h('div', { class: 'ph' }, h('img', { src: it.box ? crop(pid, it.photo_id, it.box, 360) : img(pid, it.photo_id, 320), alt: '', loading: 'lazy' })),
        h('span', { class: 'pct', title: t('tag.confidence') }, it.status === 'accepted' ? '✓' : Math.round(it.p * 100)), selected.has(it.photo_id) && h('span', { class: 'tick' }, '✓'));
      cell.style.aspectRatio = it.box ? '3/5' : '3/4';
      grid.append(cell);
    }
    refreshActions();
  }

  const done = (msg) => { toast(msg || t('common.saved')); m.close(); ctx.reload(); };
  async function split() {
    const r = await api.post(`/api/projects/${pid}/competitors/${c.id}/split`, { photo_ids: [...selected] });
    done(t('album.split.done'));
  }
  function removeDialog() {
    let where = 'unknown';
    const target = Field({ placeholder: '041', id: 'moveTo', maxlength: 4, inputmode: 'numeric' });
    const targetBox = h('div', { class: 'hidden', style: { width: '160px' } }, target.el);
    const choices = h('div', { class: 'stack' });
    const paint = () => {
      clear(choices).append(...['unknown', 'others', 'album'].map((k) => h('button', { class: 'choice', type: 'button', 'aria-pressed': String(where === k), onclick: () => { where = k; targetBox.classList.toggle('hidden', k !== 'album'); paint(); if (k === 'album') target.input.focus(); } },
        h('span', { class: 't' }, t('album.to.' + k)), h('span', { class: 'd' }, t('album.to.' + k + '.d')))));
    };
    paint();
    const dlg = modal({ title: t('album.remove.title', { n: selected.size }), body: h('div', { class: 'stack' }, choices, targetBox),
      actions: [Key(t('common.cancel'), { onclick: () => dlg.close() }), Key(t('album.remove.go'), { primary: true, onclick: async () => {
        const destination = { type: where };
        if (where === 'album') {
          const n = parseInt(target.value, 10);
          if (!(n >= 1 && n <= 9999)) { toast(t('tag.badnumber')); return; }
          destination.number = n;
        }
        await api.post(`/api/projects/${pid}/competitors/${c.id}/remove-photos`, { photo_ids: [...selected], destination });
        dlg.close(); done(where === 'unknown' ? t('album.removed.unknown') : t('common.saved'));
      } })] });
  }

  // --- header: number and name
  let m;
  const number = Field({ label: t('album.number'), value: String(c.number), id: 'albNo', maxlength: 4, inputmode: 'numeric' });
  const name = Field({ label: t('album.name'), value: c.name || '', placeholder: t('roster.name.ph'), id: 'albName', maxlength: 60 });
  async function saveHead() {
    const n = parseInt(number.value, 10);
    if (!(n >= 1 && n <= 9999)) { toast(t('tag.badnumber')); return; }
    let merge = false;
    if (n !== c.number) {
      const comps = await api.get(`/api/projects/${pid}/competitors`);
      const other = comps.find((x) => x.number === n && x.variant === 1 && x.id !== c.id);
      if (other) {
        const choice = await askMerge(other);
        if (!choice) return;
        merge = choice === 'merge';
      }
    }
    await api.put(`/api/projects/${pid}/competitors/${c.id}`, { number: n, name: name.value, merge });
    done();
  }
  const head = others ? h('div', { class: 'stack' }, h('p', { class: 'muted' }, t('album.others.body')))
    : h('div', { class: 'stack' }, h('div', { class: 'row wrap top-a' }, h('div', { style: { width: '140px' } }, number.el), h('div', { class: 'grow' }, name.el), Key(t('common.save'), { primary: true, h: 52, onclick: saveHead })),
      h('div', { class: 'row wrap' }, Tag(c.label), !items.length && Key(t('common.delete'), { h: 36, onclick: async () => { await api.del(`/api/projects/${pid}/competitors/${c.id}`); done(); } }),
        h('span', { class: 'data subtle' }, t('album.renumber.hint'))));
  m = modal({ title: others ? t('album.others') : `${c.label} ${c.name || ''}`, wide: true,
    body: h('div', { class: 'stack' }, head, h('div', { class: 'hr' }), h('div', { class: 'sticky-bar' }, h('p', { class: 'data subtle' }, t('album.select.hint')), actionsRow, count), items.length ? grid : h('p', { class: 'muted' }, t('album.none'))) });
  drawGrid();
}

function askMerge(other) {
  return new Promise((resolve) => {
    let answered = false;
    const pick = (v) => { answered = true; dlg.close(); resolve(v); };
    const dlg = modal({ title: t('album.clash.title', { n: other.label }), onclose: () => { if (!answered) resolve(null); },
      body: h('div', { class: 'stack' }, h('p', { class: 'muted' }, t('album.clash.body', { n: other.label })),
        h('button', { class: 'choice', type: 'button', onclick: () => pick('merge') }, h('span', { class: 't' }, t('album.clash.merge')), h('span', { class: 'd' }, t('album.clash.merge.d'))),
        h('button', { class: 'choice', type: 'button', onclick: () => pick('variant') }, h('span', { class: 't' }, t('album.clash.variant', { n: other.label })), h('span', { class: 'd' }, t('album.clash.variant.d')))),
      actions: [Key(t('common.cancel'), { onclick: () => dlg.close() })] });
  });
}
