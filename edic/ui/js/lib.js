// Shared helpers and the møušn components (Key, Tag, Field, Check, Orbit, Knob, Slider, DotBar) as small JS factories.
import { t, pad3 } from './i18n.js';

// ------------------------------------------------------------------ DOM
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  append(el, kids);
  return el;
}
export function svg(tag, attrs, ...kids) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) el.setAttribute(k, v);
  append(el, kids);
  return el;
}
function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k.nodeType ? k : document.createTextNode(String(k)));
  }
}
export const $ = (sel, root = document) => root.querySelector(sel);
export const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
export const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ API
export class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }
async function req(method, url, body) {
  const isForm = body instanceof FormData;
  const r = await fetch(url, {
    method, credentials: 'same-origin',
    headers: body && !isForm ? { 'content-type': 'application/json' } : undefined,
    body: isForm ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch { /* keep statusText */ }
    throw new ApiError(msg, r.status);
  }
  return r.json();
}
export const api = {
  get: (u) => req('GET', u), post: (u, b) => req('POST', u, b ?? {}), put: (u, b) => req('PUT', u, b), del: (u) => req('DELETE', u),
};
export const img = (pid, photoId, w = 640) => `/api/projects/${pid}/photos/${photoId}/image?w=${w}`;
export const crop = (pid, photoId, box, w = 480) => `/api/projects/${pid}/photos/${photoId}/crop?box=${box.map((v) => Math.round(v)).join(',')}&w=${w}`;

/** Follow a background job until it ends. onProgress gets the job dict on every tick. */
export async function watchJob(job, onProgress) {
  let j = job;
  while (j.state === 'running') {
    onProgress?.(j);
    await sleep(350);
    j = await api.get(`/api/jobs/${j.id}`);
  }
  onProgress?.(j);
  if (j.state === 'error') throw new Error(j.error || t('common.error'));
  return j;
}

// ------------------------------------------------------------ components
const CUT = 12;
function keyPoints(W, H, c = CUT) {
  return `1,1 ${W - c - 1},1 ${W - 1},${c + 1} ${W - 1},${H - 1} ${c + 1},${H - 1} 1,${H - c - 1}`;
}
const keyObserver = new ResizeObserver((entries) => {
  for (const e of entries) fitKey(e.target);
});
function fitKey(btn) {
  const W = Math.round(btn.offsetWidth), H = Math.round(btn.offsetHeight);
  if (!W || !H) return;
  const s = btn.querySelector('svg');
  s.setAttribute('viewBox', `0 0 ${W} ${H}`);
  for (const p of s.querySelectorAll('polygon')) p.setAttribute('points', keyPoints(W, H));
  const scan = btn.querySelector('.scan');
  if (scan) scan.style.clipPath = `polygon(0 0,calc(100% - ${CUT}px) 0,100% ${CUT}px,100% 100%,${CUT}px 100%,0 calc(100% - ${CUT}px))`;
}

/** The vector key: outline that a beam traces on hover. primary = the one red thing on screen. */
export function Key(label, { primary = false, legend = '', onclick, h: height = 48, w, disabled = false, tall = false, title, type = 'button' } = {}) {
  const lbl = h('span', { class: 'lbl' }, h('span', { class: 'lb' }, label), legend && h('span', { class: 'lg' }, legend));
  const btn = h('button', { class: `ms-key${primary ? ' primary' : ''}${tall ? ' tall' : ''}`, type, disabled, title: title || null, onclick, style: { height: height + 'px', ...(w ? { width: w + 'px' } : { minWidth: '96px' }) } },
    svg('svg', { preserveAspectRatio: 'none', 'aria-hidden': 'true' },
      svg('polygon', { class: 'edge', points: '0,0' }), svg('polygon', { class: 'draw', points: '0,0', pathLength: 100 })),
    primary && h('span', { class: 'scan' }), lbl);
  keyObserver.observe(btn);
  requestAnimationFrame(() => fitKey(btn));
  return btn;
}

/** Outline label with cut corners. kind: 'fill' | 'cobalt' | 'amber' | 'live' | 'signal'. */
export function Tag(label, { kind = '', lg = false, onclick, pressed, title } = {}) {
  const cls = `ms-tag${lg ? ' lg' : ''}${kind === 'fill' ? ' fill' : ''}`;
  const color = { cobalt: 'var(--cobalt-text)', amber: 'var(--amber)', signal: 'var(--signal-hot)', muted: 'var(--text-subtle)' }[kind];
  const kids = [kind === 'live' && h('span', { class: 'led' }), label];
  if (onclick) {
    return h('button', { class: cls, type: 'button', onclick, 'aria-pressed': pressed == null ? null : String(!!pressed), title, style: color ? { color } : null }, kids);
  }
  return h('span', { class: cls, title, style: color ? { color } : null }, kids);
}

/** Text field written in Bitcount: HUD corners on focus, one dot lit per character. */
export function Field({ label, value = '', placeholder = '', type = 'text', onInput, onEnter, id, maxlength = 40, inputmode, autofocus = false } = {}) {
  const input = h('input', { type, value, placeholder, id, maxlength, inputmode, autocomplete: 'off', spellcheck: 'false' });
  const dots = h('div', { class: 'dots' }, Array.from({ length: 24 }, () => h('span')));
  const paint = () => {
    const n = Math.min(24, input.value.length);
    [...dots.children].forEach((d, i) => { d.className = i < n - 1 ? 'on' : i === n - 1 ? 'on last' : ''; });
  };
  input.addEventListener('input', () => { paint(); onInput?.(input.value); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') onEnter?.(input.value); });
  const el = h('div', { class: 'ms-field' },
    label && h('label', { class: 'ms-caption', for: id }, label),
    h('div', { class: 'box' }, ['a', 'b', 'c', 'd'].map((c) => h('span', { class: `tk ${c}` })), input), dots);
  paint();
  if (autofocus) requestAnimationFrame(() => input.focus());
  return { el, input, get value() { return input.value; }, set value(v) { input.value = v; paint(); } };
}

/** Saturn checkbox: ticking adds the ring, together they form the ø. */
export function Check({ checked = false, label, onchange } = {}) {
  const b = h('button', { class: 'ms-check', type: 'button', role: 'checkbox', 'aria-checked': String(checked), 'aria-label': label },
    h('span', { class: 'pl' }), h('span', { class: 'rg' }));
  b.addEventListener('click', () => { const v = b.getAttribute('aria-checked') !== 'true'; b.setAttribute('aria-checked', String(v)); onchange?.(v); });
  const row = h('div', { class: 'check-row' }, b, label && h('span', { class: 'check-label' }, label));
  row.set = (v) => b.setAttribute('aria-checked', String(v));
  row.button = b;
  return row;
}

/** Planet that flies to the other side of its orbit when switched on. */
export function Orbit({ checked = false, on = '', off = '', label, onchange } = {}) {
  const state = h('div', { class: 'orbit-state' }, checked ? on : off);
  const b = h('button', { class: 'ms-orbit', type: 'button', role: 'switch', 'aria-checked': String(checked), 'aria-label': label },
    h('span', { class: 'arm' }, h('i')), h('span', { class: 'core' }));
  b.addEventListener('click', () => {
    const v = b.getAttribute('aria-checked') !== 'true';
    b.setAttribute('aria-checked', String(v));
    state.textContent = v ? on : off;
    onchange?.(v);
  });
  const el = h('div', { class: 'orbit-wrap' }, b, state);
  el.set = (v) => { b.setAttribute('aria-checked', String(v)); state.textContent = v ? on : off; };
  return el;
}

/** Knob with a moon as indicator and a dotted scale. Drag up/down, or use the arrow keys. */
export function Knob({ value = 50, onchange, label, color = 'var(--signal)', big = true, min = 0, max = 100 } = {}) {
  const box = big ? 170 : 110, kn = big ? 100 : 64, moon = big ? 14 : 10, N = big ? 25 : 17;
  let v = value;
  const rot = h('div', { class: 'rot' }, h('span', { class: 'moon', style: { width: moon + 'px', height: moon + 'px', top: '5px', marginLeft: -moon / 2 + 'px', background: color, color } }));
  const btn = h('button', { class: 'ms-knob', type: 'button', role: 'slider', 'aria-valuemin': min, 'aria-valuemax': max, 'aria-label': label, style: { width: kn + 'px', height: kn + 'px', left: (box - kn) / 2 + 'px', top: (box - kn) / 2 + 'px' } }, rot);
  const dots = Array.from({ length: N }, (_, i) => {
    const a = (-135 + (i / (N - 1)) * 270) * Math.PI / 180, R = box / 2 - 8;
    return h('span', { class: 'rdot', style: { left: box / 2 + R * Math.sin(a) + 'px', top: box / 2 - R * Math.cos(a) + 'px' } });
  });
  const val = h('div', { class: 'knob-val' });
  const wrap = h('div', { class: 'knob' }, h('div', { class: 'ms-knob-wrap', style: { width: box + 'px', height: box + 'px' } }, dots, btn), val,
    label && h('div', { class: 'ms-caption' }, label));
  const paint = () => {
    const f = (v - min) / (max - min);
    rot.style.transform = `rotate(${-135 + f * 270}deg)`;
    dots.forEach((d, i) => { const on = i / (N - 1) <= f + 1e-6; d.classList.toggle('on', on); d.style.background = on && i === Math.round(f * (N - 1)) ? color : ''; });
    btn.setAttribute('aria-valuenow', Math.round(v));
    val.textContent = pad3(Math.round(v));
  };
  const set = (nv, emit = true) => { v = Math.max(min, Math.min(max, nv)); paint(); if (emit) onchange?.(Math.round(v)); };
  let drag = null;
  btn.addEventListener('pointerdown', (e) => { btn.setPointerCapture(e.pointerId); drag = { y: e.clientY, v }; });
  btn.addEventListener('pointermove', (e) => { if (drag) set(drag.v + (drag.y - e.clientY) * 0.6 * ((max - min) / 100), false); });
  const end = () => { if (drag) { drag = null; onchange?.(Math.round(v)); } };
  btn.addEventListener('pointerup', end); btn.addEventListener('pointercancel', end);
  btn.addEventListener('keydown', (e) => {
    const d = { ArrowUp: 5, ArrowRight: 5, ArrowDown: -5, ArrowLeft: -5 }[e.key];
    if (d) { e.preventDefault(); set(v + d); }
  });
  paint();
  wrap.set = (nv) => set(nv, false);
  return wrap;
}

/** Native range input with the dotted track. */
export function Slider({ label, min = 0, max = 100, value = 50, step = 1, onchange, format = (v) => pad3(v) } = {}) {
  const out = h('span', { class: 'ms-caption', style: { color: 'var(--text)' } }, format(value));
  const input = h('input', { class: 'ms-slider', type: 'range', min, max, step, value, 'aria-label': label });
  input.addEventListener('input', () => { out.textContent = format(+input.value); onchange?.(+input.value); });
  return h('div', { class: 'slider-wrap' }, h('div', { class: 'slider-head' }, h('span', { class: 'ms-caption' }, label), out), input);
}

/** Row of dots that light up like a progress bar. The last lit one is the red one. */
export function DotBar({ value = 0, n = 40, label } = {}) {
  const dots = Array.from({ length: n }, () => h('i'));
  const el = h('div', { class: 'dotbar', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-label': label || null }, dots);
  el.set = (f) => {
    f = Math.max(0, Math.min(1, f || 0));
    const lit = Math.floor(f * n + 1e-9);
    dots.forEach((d, i) => { d.className = i < lit - 1 ? 'on' : i === lit - 1 ? 'on cur' : ''; });
    el.setAttribute('aria-valuenow', Math.round(f * 100));
  };
  el.set(value);
  return el;
}

// ---------------------------------------------------------------- overlays
export function toast(msg, { kind = '' } = {}) {
  let host = $('#toasts');
  if (!host) { host = h('div', { id: 'toasts', 'aria-live': 'polite' }); document.body.append(host); }
  const el = h('div', { class: `toast ${kind}` }, msg);
  host.append(el);
  setTimeout(() => el.classList.add('out'), 3200);
  setTimeout(() => el.remove(), 3700);
}

export function modal({ title, body, actions = [], wide = false, onclose } = {}) {
  const close = () => { overlay.remove(); document.removeEventListener('keydown', esc); onclose?.(); };
  const esc = (e) => { if (e.key === 'Escape') close(); };
  const panel = h('div', { class: `modal panel${wide ? ' wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('div', { class: 'panel-in' },
      h('div', { class: 'modal-head' }, h('h2', { class: 'title' }, title), h('button', { class: 'x', type: 'button', 'aria-label': t('common.close'), onclick: close }, '×')),
      h('div', { class: 'modal-body' }, body),
      actions.length ? h('div', { class: 'modal-actions' }, actions) : null));
  const overlay = h('div', { class: 'overlay', onmousedown: (e) => { if (e.target === overlay) close(); } }, panel);
  document.body.append(overlay);
  document.addEventListener('keydown', esc);
  return { close, el: panel };
}

/** Dots fly out of a point: the design system's answer to confetti. */
export function burst(x, y, { n = 16, color = 'var(--signal)' } = {}) {
  if (reducedMotion()) return;
  for (let i = 0; i < n; i++) {
    const d = h('i', { class: 'burst-dot', style: { left: x + 'px', top: y + 'px', background: i % 4 === 0 ? color : 'var(--text)' } });
    document.body.append(d);
    const a = (i / n) * Math.PI * 2 + Math.random() * 0.4, dist = 50 + Math.random() * 70;
    d.animate([{ transform: 'translate(-50%,-50%) scale(1)', opacity: 1 },
      { transform: `translate(calc(-50% + ${Math.cos(a) * dist}px), calc(-50% + ${Math.sin(a) * dist}px)) scale(.4)`, opacity: 0 }],
      { duration: 800 + Math.random() * 200, easing: 'cubic-bezier(.3,1.2,.5,1)' }).onfinish = () => d.remove();
  }
}
export function burstAt(el, opts) { const r = el.getBoundingClientRect(); burst(r.left + r.width / 2, r.top + r.height / 2, opts); }

/** Server-side folder browser (a browser cannot hand over a real path). Resolves to a path or null. */
export function pickFolder({ start = '', title = t('new.pick') } = {}) {
  return new Promise((resolve) => {
    let cur = start, done = false;
    const list = h('div', { class: 'fs-list' });
    const pathBox = h('div', { class: 'fs-path data' });
    const info = h('div', { class: 'ms-caption' });
    const choose = Key(t('common.choose'), { primary: true, onclick: () => { done = true; m.close(); resolve(cur); } });
    const body = h('div', { class: 'fs' }, pathBox, info, list);
    const m = modal({ title, body, actions: [choose], onclose: () => { if (!done) resolve(null); } });
    async function go(p) {
      try {
        const d = await api.get('/api/fs?path=' + encodeURIComponent(p));
        cur = d.path; pathBox.textContent = d.path;
        info.textContent = t('new.here', { n: d.images });
        clear(list);
        if (d.parent) list.append(h('button', { class: 'fs-row', type: 'button', onclick: () => go(d.parent) }, h('span', { class: 'ms-caption' }, '↑'), t('new.up')));
        for (const dr of d.drives || []) list.append(h('button', { class: 'fs-row', type: 'button', onclick: () => go(dr) }, h('span', { class: 'fs-dot' }), dr));
        for (const x of d.dirs) list.append(h('button', { class: 'fs-row', type: 'button', onclick: () => go(x.path) }, h('span', { class: 'fs-dot' }), x.name));
      } catch (e) { toast(e.message); }
    }
    go(start);
  });
}

export const evidenceChips = (ev) => {
  if (!ev) return [];
  const out = [];
  if (ev.num > 0) out.push(Tag(`${t('ev.num')}`, { kind: 'fill' }));
  if (ev.face > 0.4) out.push(Tag(`${t('ev.face')} ${Math.round(ev.face * 100)}`, { kind: 'cobalt' }));
  if (ev.colour > 0.8) out.push(Tag(`${t('ev.colour')}`, { kind: 'amber' }));
  if (ev.seq) out.push(Tag(`${t('ev.seq')}`, { kind: 'muted' }));
  return out;
};
