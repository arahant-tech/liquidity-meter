/* Judgment screen: where liquidity sits on the scalar × vector plane. Measurement only. */
(() => {
'use strict';
const CELLS = ['A', 'B+', 'C+', 'C-', 'B-', 'D'];
const SHORT = { 'A': '총량↑ 방향↑', 'B+': '총량↑ 방향↓', 'B-': '총량↑ 방향↓', 'C+': '총량↓ 방향↑', 'C-': '총량↓ 방향↑', 'D': '총량↓ 방향↓' };
const SIDE = { 'A': '준비금↑', 'B+': '준비금↑', 'C+': '준비금↑', 'B-': '준비금↓', 'C-': '준비금↓', 'D': '준비금↓' };
const STATUS = { ok: ['정상', 'ok'], stale: ['갱신 지연', 'stale'], retained: ['이전 정상값', 'retained'], unavailable: ['자료 없음', 'error'] };
const STUDY_URL = './research/studies/quadrant-2026-11/results.json';
const STALE_DAYS = 14;
const up = (cell) => SIDE[cell] === '준비금↑';
const finite = (x) => typeof x === 'number' && Number.isFinite(x);
const validDate = (s) => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s) && new Date(s + 'T00:00:00Z').toISOString().slice(0, 10) === s;

/* Same rule as lm/measure/quadrant.py. Zero counts as "not up". */
function classify(s, v) {
  const g = s + v;
  if (s > 0 && v > 0) return 'A';
  if (s > 0) return g > 0 ? 'B+' : 'B-';
  if (v > 0) return g > 0 ? 'C+' : 'C-';
  return 'D';
}

/* Refuse to draw a map that is malformed or disagrees with itself. */
function validate(q, now = Date.now()) {
  if (!q || q.schema_version !== 1 || !(q.status in STATUS)) throw Error('지도 자료의 형식을 확인할 수 없습니다.');
  if (q.status === 'unavailable') return { status: 'unavailable', reason: q.reason || null, points: [] };
  if (!Array.isArray(q.points) || !q.points.length || !q.now) throw Error('지도 자료가 비어 있습니다.');
  let previous = '';
  for (const p of q.points) {
    if (!validDate(p.d) || p.d <= previous) throw Error('지도 자료의 날짜 순서가 맞지 않습니다.');
    if (!finite(p.s) || !finite(p.v) || !CELLS.includes(p.c)) throw Error('지도 자료에 읽을 수 없는 값이 있습니다.');
    if (p.b !== null && ![1, 2, 3, 4, 5].includes(p.b)) throw Error('지도 자료의 구간 값이 잘못됐습니다.');
    // rounding can move a point across a boundary only when it sits on it
    if (classify(p.s, p.v) !== p.c && Math.min(Math.abs(p.s), Math.abs(p.v), Math.abs(p.s + p.v)) > 0.002) throw Error('지도 자료의 칸이 값과 맞지 않습니다.');
    previous = p.d;
  }
  const last = q.points[q.points.length - 1];
  if (q.asof !== last.d || q.now.date !== last.d || q.now.cell !== last.c) throw Error('지도 자료의 기준 주가 서로 다릅니다.');
  if (Date.parse(last.d + 'T00:00:00Z') > now + 864e5) throw Error('지도 자료에 미래 날짜가 있습니다.');
  const age = Math.floor((now - Date.parse(last.d + 'T00:00:00Z')) / 864e5);
  const status = q.status === 'ok' && age > STALE_DAYS ? 'stale' : q.status;   // never trust "ok" on an old map
  return { ...q, status, age, points: q.points.map((p) => ({ ...p, g: p.s + p.v })) };
}

if (typeof module !== 'undefined') module.exports = { classify, validate, CELLS, SHORT, SIDE };
if (typeof document === 'undefined') return;

const $ = (id) => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const el = (tag, attrs = {}, text) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); if (text != null) n.textContent = text; return n; };
const html = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const fmt = (x, d = 1) => (x >= 0 ? '+' : '−') + Math.abs(x).toFixed(d);
const kst = (iso) => { const d = new Date(iso); return Number.isFinite(d.getTime()) ? new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(d) + ' KST' : '시각 미확인'; };
const tip = $('tip');
function showTip(evt, lines) {
  tip.replaceChildren();
  lines.forEach((t, i) => tip.append(html(i ? 'div' : 'b', '', t)));
  tip.hidden = false;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = evt.clientX + 14, y = evt.clientY + 14;
  if (x + w > innerWidth - 8) x = evt.clientX - w - 14;
  if (y + h > innerHeight - 8) y = evt.clientY - h - 14;
  tip.style.left = Math.max(8, x) + 'px'; tip.style.top = Math.max(8, y) + 'px';
}
const hideTip = () => { tip.hidden = true; };
let state = null;

function drawPlane(host, opt) {
  const W = Math.max(280, Math.min(host.clientWidth || 600, opt.max));
  const m = { l: 44, r: 10, t: 10, b: 34 };
  const f = opt.asinh ? (x) => Math.asinh(x / 1.5) : (x) => x;
  const [x0, x1, y0, y1] = opt.range.map(f);
  const k = (W - m.l - m.r) / (x1 - x0), H = Math.round(m.t + m.b + (y1 - y0) * k);
  const px = (t) => m.l + (t - x0) * k, py = (t) => m.t + (y1 - t) * k;
  const X = (s) => px(f(s)), Y = (v) => py(f(v));
  const left = m.l, right = W - m.r, top = m.t, bottom = H - m.b;
  const svg = el('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': opt.label });
  const da = Math.max(x0, -y1), db = Math.min(x1, -y0);          // where the diagonal s + v = 0 enters and leaves the plot
  svg.append(el('polygon', { points: `${px(da)},${py(-da)} ${px(db)},${py(-db)} ${right},${py(-db)} ${right},${top} ${px(da)},${top}`, fill: 'var(--pos)', opacity: .07 }));
  svg.append(el('polygon', { points: `${px(da)},${py(-da)} ${px(db)},${py(-db)} ${px(db)},${bottom} ${left},${bottom} ${left},${py(-da)}`, fill: 'var(--neg)', opacity: .07 }));
  for (const t of opt.ticks) {
    if (f(t) >= x0 && f(t) <= x1) {
      svg.append(el('line', { x1: X(t), x2: X(t), y1: top, y2: bottom, stroke: t === 0 ? 'var(--guide)' : 'var(--grid)', 'stroke-width': 1 }));
      svg.append(el('text', { x: X(t), y: bottom + 14, 'text-anchor': 'middle' }, (t > 0 ? '+' : '') + t));
    }
    if (f(t) >= y0 && f(t) <= y1) {
      svg.append(el('line', { x1: left, x2: right, y1: Y(t), y2: Y(t), stroke: t === 0 ? 'var(--guide)' : 'var(--grid)', 'stroke-width': 1 }));
      svg.append(el('text', { x: left - 6, y: Y(t) + 4, 'text-anchor': 'end' }, (t > 0 ? '+' : '') + t));
    }
  }
  svg.append(el('line', { x1: px(da), y1: py(-da), x2: px(db), y2: py(-db), stroke: 'var(--muted)', 'stroke-width': 1.5 }));
  svg.append(el('text', { x: (left + right) / 2, y: H - 4, 'text-anchor': 'middle' }, '총량 (총자산 13주 변화, %) →'));
  svg.append(el('text', { x: 10, y: (top + bottom) / 2, 'text-anchor': 'middle', transform: `rotate(-90 10 ${(top + bottom) / 2})` }, '방향 (준비금 쪽으로 옮겨 온 돈, %) →'));
  const inside = (p) => f(p.s) >= x0 && f(p.s) <= x1 && f(p.v) >= y0 && f(p.v) <= y1;
  for (const p of opt.points) if (inside(p)) svg.append(el('circle', { cx: X(p.s), cy: Y(p.v), r: opt.r, fill: p.g > 0 ? 'var(--pos)' : 'var(--neg)', opacity: opt.opacity }));
  if (opt.path) svg.append(el('polyline', { points: opt.trail.map((p) => `${X(p.s)},${Y(p.v)}`).join(' '), fill: 'none', stroke: 'var(--text)', 'stroke-width': 1.6, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', opacity: .9 }));
  const last = opt.trail[opt.trail.length - 1];
  svg.append(el('circle', { cx: X(last.s), cy: Y(last.v), r: 6, fill: 'var(--text)', stroke: 'var(--panel)', 'stroke-width': 2 }));
  for (const [tx, ty, c] of opt.labels) {
    const xx = px(opt.asinh ? tx : f(tx)), yy = py(opt.asinh ? ty : f(ty));
    svg.append(el('text', { x: xx, y: yy, 'text-anchor': 'middle', class: 'strong halo' }, SHORT[c]));
    svg.append(el('text', { x: xx, y: yy + 14, 'text-anchor': 'middle', class: 'halo' }, SIDE[c] + (opt.shares ? ` · ${Math.round(100 * opt.shares[c].share)}%` : '')));
  }
  for (const [p, text, dx, dy] of opt.marks) svg.append(el('text', { x: X(p.s) + dx, y: Y(p.v) + dy, class: 'strong halo' }, text));
  const hit = el('rect', { x: left, y: top, width: right - left, height: bottom - top, fill: 'transparent' });
  const ring = el('circle', { r: 7, fill: 'none', stroke: 'var(--text)', 'stroke-width': 1.5, visibility: 'hidden' });
  hit.addEventListener('pointermove', (evt) => {
    const b = svg.getBoundingClientRect(), mx = (evt.clientX - b.left) * (W / b.width), my = (evt.clientY - b.top) * (H / b.height);
    let best = null, bd = 18 * 18;
    for (const p of opt.points) { if (!inside(p)) continue; const d = (X(p.s) - mx) ** 2 + (Y(p.v) - my) ** 2; if (d < bd) { bd = d; best = p; } }
    if (!best) { ring.setAttribute('visibility', 'hidden'); return hideTip(); }
    ring.setAttribute('cx', X(best.s)); ring.setAttribute('cy', Y(best.v)); ring.setAttribute('visibility', 'visible');
    showTip(evt, [best.d, `${SHORT[best.c]} · ${SIDE[best.c]}`, `총량 ${fmt(best.s, 2)}  방향 ${fmt(best.v, 2)}`, `준비금 변화 ${fmt(best.g, 2)}` + (best.b ? ` · ${best.b}구간` : '')]);
  });
  hit.addEventListener('pointerleave', () => { ring.setAttribute('visibility', 'hidden'); hideTip(); });
  svg.append(ring, hit);
  host.replaceChildren(svg);
}

function drawRaster(host, pts) {
  const W = Math.max(640, host.clientWidth || 760), rowH = 20, m = { l: 138, r: 8, t: 6, b: 22 };
  const H = m.t + m.b + rowH * 6 + 8;
  const svg = el('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': '2009년 이후 주별로 어느 칸에 있었는지' });
  const t0 = Date.parse(pts[0].d), t1 = Date.parse(pts[pts.length - 1].d) + 7 * 864e5;
  const X = (d) => m.l + (W - m.l - m.r) * (Date.parse(d) - t0) / (t1 - t0);
  const wk = (W - m.l - m.r) * 7 * 864e5 / (t1 - t0);
  const rowY = (i) => m.t + i * rowH + (i >= 3 ? 8 : 0);
  CELLS.forEach((c, i) => {
    svg.append(el('text', { x: m.l - 8, y: rowY(i) + 13, 'text-anchor': 'end' }, `${SHORT[c]} · ${SIDE[c]}`));
    svg.append(el('rect', { x: m.l, y: rowY(i) + 2, width: W - m.l - m.r, height: rowH - 4, fill: 'var(--surface)' }));
  });
  for (const p of pts) svg.append(el('rect', { x: X(p.d), y: rowY(CELLS.indexOf(p.c)) + 2, width: Math.max(1, wk + .3), height: rowH - 4, fill: up(p.c) ? 'var(--pos)' : 'var(--neg)' }));
  const y0 = new Date(t0).getUTCFullYear() + 1, y1 = new Date(t1).getUTCFullYear();
  for (let y = y0 + (y0 % 2); y <= y1; y += 2) {
    const x = X(`${y}-01-01`);
    svg.append(el('line', { x1: x, x2: x, y1: m.t, y2: H - m.b, stroke: 'var(--panel)', 'stroke-width': 1 }));
    svg.append(el('text', { x, y: H - 6, 'text-anchor': 'middle' }, y));
  }
  const hit = el('rect', { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: 'transparent' });
  hit.addEventListener('pointermove', (evt) => {
    const b = svg.getBoundingClientRect(), mx = (evt.clientX - b.left) * (W / b.width), t = t0 + (mx - m.l) / (W - m.l - m.r) * (t1 - t0);
    let best = pts[0]; for (const p of pts) { if (Date.parse(p.d) <= t) best = p; else break; }
    showTip(evt, [best.d, `${SHORT[best.c]} · ${SIDE[best.c]}`, `총량 ${fmt(best.s, 2)}  방향 ${fmt(best.v, 2)}  준비금 ${fmt(best.g, 2)}`]);
  });
  hit.addEventListener('pointerleave', hideTip);
  svg.append(hit);
  host.replaceChildren(svg);
}

function drawTiles(q) {
  const n = q.now, host = $('tiles'); host.replaceChildren();
  const add = (big, small) => { const d = html('div', 'tile'); d.append(html('b', '', big), html('span', '', small)); host.append(d); };
  const lv = n.levels_billion_usd;
  add(SHORT[n.cell], `지금의 칸 · ${SIDE[n.cell]}`);
  add(fmt(n.g, 2) + '%', '준비금 13주 변화');
  add(fmt(n.s, 2) + '%', '총량');
  add(fmt(n.v, 2) + '%', '방향');
  add(n.bucket ? `${n.bucket} / 5` : '—', '준비금 변화의 구간(과거 대비)');
  add(Math.round(lv.R).toLocaleString('en-US'), '준비금 (십억 달러)');
  add(lv.RRP.toFixed(1), '국내 역레포 (십억 달러)');
  add(Math.round(lv.TGA).toLocaleString('en-US'), '재무부 계정 TGA (십억 달러)');
  const src = n.vector_sources;
  $('sources-note').textContent = `방향 ${fmt(n.v, 2)}의 출처: 국내 역레포 ${fmt(src.rrp, 2)}, 재무부 계정 ${fmt(src.tga, 2)}, 해외 공식 역레포 ${fmt(src.frrp, 2)}, 현금통화 ${fmt(src.cur, 2)}, 기타 ${fmt(src.other, 2)}.`
    + (Math.abs(n.s) < 0.1 ? ' 총량이 0에 가까워 칸의 경계에 걸쳐 있다.' : '');
}

function drawRecent(pts) {
  const t = $('recent'), head = html('tr');
  ['기준 주', '칸', '총량', '방향', '준비금 변화', '구간'].forEach((h) => head.append(html('th', '', h)));
  const thead = html('thead'); thead.append(head); const body = html('tbody');
  for (const p of pts.slice(-12).reverse()) {
    const tr = html('tr'), cell = html('td'), mark = html('span', 'mark');
    mark.style.background = up(p.c) ? 'var(--pos)' : 'var(--neg)';
    cell.append(mark, document.createTextNode(`${SHORT[p.c]} · ${SIDE[p.c]}`));
    tr.append(html('td', '', p.d), cell, html('td', '', fmt(p.s, 2)), html('td', '', fmt(p.v, 2)), html('td', '', fmt(p.g, 2)), html('td', '', p.b ? String(p.b) : '—'));
    body.append(tr);
  }
  t.replaceChildren(thead, body);
}

function drawStudy(study, current) {
  const R = study && study.cell_returns, assets = ['SPY', 'QQQ', 'QQQ−SPY', 'BTC', 'ETH'];
  if (!R || !CELLS.every((c) => R[c]) || !R.all) return;
  const t = $('heat'), head = html('tr');
  head.append(html('th', '', '칸'), html('th', '', '주')); assets.forEach((a) => head.append(html('th', '', a)));
  const thead = html('thead'); thead.append(head); const body = html('tbody');
  const maxDev = Object.fromEntries(assets.map((a) => [a, Math.max(...CELLS.map((c) => R[c][a] ? Math.abs(R[c][a].fwd13 - R.all[a].fwd13) : 0))]));
  const row = (key, name, sub, cls) => {
    const tr = html('tr', cls), td = html('td'), nm = html('span', 'cellname'), title = html('span', '', name);
    if (CELLS.includes(key)) { const d = html('span', 'mark'); d.style.background = up(key) ? 'var(--pos)' : 'var(--neg)'; title.prepend(d); }
    if (key === current) title.append(html('span', 'here', '지금'));
    nm.append(title); if (sub) nm.append(html('small', '', sub)); td.append(nm); tr.append(td);
    tr.append(html('td', '', R[key].SPY ? String(R[key].SPY.n) : ''));
    assets.forEach((a) => {
      const r = R[key][a], c = html('td');
      if (!r) { c.textContent = '—'; tr.append(c); return; }
      c.append(document.createTextNode(fmt(r.fwd13)), html('small', '', Math.round(100 * r.hit) + '%'));
      if (CELLS.includes(key)) {
        const dev = (r.fwd13 - R.all[a].fwd13) / (maxDev[a] || 1), pct = Math.round(Math.min(1, Math.abs(dev)) * 42);
        c.style.background = `color-mix(in oklab, var(${dev >= 0 ? '--pos' : '--neg'}) ${pct}%, var(--panel))`;
      }
      tr.append(c);
    });
    body.append(tr);
  };
  CELLS.forEach((c) => row(c, SHORT[c], SIDE[c], c === current ? 'current' : ''));
  row('g>0', '준비금↑ 전체', '대각선 위', 'sum'); row('g<=0', '준비금↓ 전체', '대각선 아래'); row('all', '전체', '');
  t.replaceChildren(thead, body);
  $('study-lead').textContent = `기준 수요일 자료가 공표된 뒤 금요일 종가에 들어가 13주 보유했을 때의 평균 로그수익률(%)과 수익이 난 주의 비율. 색은 각 자산의 전체 평균보다 좋았는지(파랑) 나빴는지(빨강)를 나타낸다. ${study.asof} 기준으로 고정된 연구 결과이며 매주 다시 계산하지 않는다.`;
  $('study-note').textContent = '여섯 칸 × 다섯 자산을 한꺼번에 본 탐색이므로 개별 칸의 차이는 우연일 수 있다. 사전에 정한 검정 가운데 보정 후에도 남은 것은 "같은 13주 안에서 BTC는 총량보다 방향에 더 크게 움직였다" 하나다. 한 칸의 평균은 긴 구간 한두 개가 좌우한다. 주식과 BTC는 2014년 10월 이후, ETH는 2017년 11월 이후다.';
  $('study').hidden = false;
}

function drawCharts() {
  if (!state) return;
  const pts = state.points, trail = pts.slice(-52), last = trail[trail.length - 1];
  drawPlane($('plane-all'), { asinh: true, range: [-18, 75, -36, 22], ticks: [-20, -10, -5, -2, 0, 2, 5, 10, 20, 50], points: pts, r: 2.3, opacity: .4, trail, path: false, shares: state.cells,
    labels: [[2.9, 2.6, 'A'], [3.6, -1.1, 'B+'], [1.5, -3.3, 'B-'], [-1.6, 2.7, 'C+'], [-2.6, 0.85, 'C-'], [-1.9, -2.6, 'D']], marks: [[last, '지금', 9, -8]], label: '총량과 방향 평면에 놓은 2009년 이후 모든 주', max: 600 });
  const lim = Math.ceil(Math.max(...trail.map((p) => Math.max(Math.abs(p.s), Math.abs(p.v)))) + .4);
  drawPlane($('plane-zoom'), { asinh: false, range: [-lim, lim, -lim, lim], ticks: Array.from({ length: 2 * lim + 1 }, (_, i) => i - lim).filter((t) => t % (lim > 5 ? 2 : 1) === 0), points: trail, r: 3.4, opacity: .9, trail, path: true,
    labels: [[lim * .58, lim * .82, 'A'], [-lim * .58, -lim * .78, 'D']], marks: [[last, '지금', 9, -8], [trail[0], trail[0].d.slice(0, 7), 8, 4]], label: '최근 52주의 총량과 방향 경로', max: 480 });
  drawRaster($('raster'), pts);
}

function setState(q) {
  const [label, cls] = STATUS[q.status];
  const badge = $('state'); badge.textContent = label; badge.className = 'badge ' + cls;
  $('asof').textContent = q.asof || '—';
  $('generated-at').textContent = q.generated_at ? kst(q.generated_at) : '—';
  const banner = $('banner'), messages = {
    stale: `마지막 기준 주가 ${q.age}일 전이다. 새 H.4.1 자료가 아직 들어오지 않았다.`,
    retained: `이번 계산에 실패해 ${q.asof} 기준의 마지막 정상 지도를 보여 준다. 사유: ${q.reason || '미확인'}.`,
    unavailable: `사분면 자료를 아직 만들지 못했다. 사유: ${q.reason || '미확인'}. 이력 수집이 끝나면 나타난다.`,
  };
  banner.textContent = messages[q.status] || ''; banner.hidden = !messages[q.status];
}

function fail(message) {
  const badge = $('state'); badge.textContent = '자료 없음'; badge.className = 'badge error';
  $('banner').textContent = message; $('banner').hidden = false; $('content').hidden = true;
}

async function get(path) {
  const response = await fetch(path + '?t=' + Date.now(), { cache: 'no-store', credentials: 'omit' });
  if (!response.ok) throw Error(String(response.status));
  return response.json();
}

async function start() {
  let q;
  try { q = validate(await get('./quadrant.json')); }
  catch (error) { return fail(/^\d+$/.test(error.message) ? '사분면 자료를 아직 만들지 못했다. 이력 수집이 끝나면 나타난다.' : error.message); }
  setState(q);
  if (q.status === 'unavailable') { $('content').hidden = true; return; }
  state = q;
  $('content').hidden = false;
  drawTiles(q); drawRecent(q.points); drawCharts();
  try { drawStudy(await get(STUDY_URL), q.now.cell); } catch { /* the map stands on its own without the study table */ }
}

let timer;
addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(drawCharts, 150); });
start();
})();
