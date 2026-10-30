// A short walk through the whole job. Shown once on first launch, again from the "?" in the header and from Settings.
import { t } from './i18n.js';
import { h, api, clear, Key, Tag, modal } from './lib.js';
import { S } from './app.js';

const cap = (k, label) => h('div', { class: 'keycap' }, h('b', null, k), h('span', null, label));

const SLIDES = [
  { id: 'folder', visual: () => h('div', { class: 'drop has', style: { cursor: 'default' } }, h('div', null, t('tutorial.folder.visual'))) },
  { id: 'numbers', visual: () => h('div', { class: 'tut-bib' }, h('div', { class: 'box' }), h('span', null, '041')) },
  { id: 'run', visual: () => h('div', { class: 'row wrap' }, Tag('CPU / GPU', { kind: 'cobalt' }), Tag('Claude'), Tag('ChatGPT'), Tag('Gemini')) },
  { id: 'tag', visual: () => h('div', { class: 'keycaps' }, cap('←', t('tutorial.k.back')), cap('→', t('tutorial.k.next')), cap('↑', t('tutorial.k.yes')), cap('↓', t('tutorial.k.no')),
      cap('041 ⏎', t('tutorial.k.number')), cap('-', t('tutorial.k.others')), cap('Tab', t('tutorial.k.person')), cap('Ctrl Z', t('tutorial.k.undo'))) },
  { id: 'albums', visual: () => h('div', { class: 'row wrap' }, Tag('041'), Tag('041_2'), Tag('042'), Tag(t('album.others'))) },
];

export function showTutorial({ first = false } = {}) {
  let i = 0;
  const holder = h('div', { class: 'stack' });
  const dots = h('div', { class: 'row', style: { gap: '6px' } });
  let m;
  const finish = async () => {
    m.close();
    if (!S.system.settings.tutorial_seen) { S.system.settings.tutorial_seen = true; try { await api.put('/api/settings', { tutorial_seen: true }); } catch { /* not important */ } }
  };
  const draw = () => {
    const s = SLIDES[i];
    clear(holder).append(h('div', { class: 'ms-caption' }, `${i + 1} / ${SLIDES.length}`), h('h3', { class: 'title' }, t(`tutorial.${s.id}.title`)),
      h('p', { class: 'body-l muted' }, t(`tutorial.${s.id}.body`)), h('div', { class: 'tut-visual' }, s.visual()), ...(s.id === 'tag' ? [h('p', { class: 'data subtle' }, t('tutorial.tag.note'))] : []));
    clear(dots).append(...SLIDES.map((_, k) => h('i', { class: `tdot${k === i ? ' on' : ''}` })));
    next.querySelector('.lb').textContent = i === SLIDES.length - 1 ? t('tutorial.done') : t('common.next');
    prev.disabled = i === 0;
  };
  const prev = Key(t('common.back'), { h: 44, onclick: () => { i = Math.max(0, i - 1); draw(); } });
  const next = Key(t('common.next'), { primary: true, h: 44, onclick: () => { if (i === SLIDES.length - 1) finish(); else { i++; draw(); } } });
  m = modal({ title: t('tutorial.title'), body: h('div', { class: 'stack' }, holder, dots), onclose: () => { if (!S.system.settings.tutorial_seen) { S.system.settings.tutorial_seen = true; api.put('/api/settings', { tutorial_seen: true }).catch(() => {}); } },
    actions: [first && Key(t('tutorial.skip'), { h: 44, onclick: finish }), prev, next].filter(Boolean) });
  draw();
}
