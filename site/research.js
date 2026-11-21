/* Research and verification page: the record's counts, the protocols, and the list of studies. It makes no verdict. */
(() => {
'use strict';
const { ledgerSummary, get, html, fmt, kst, validDate } = window.LM;
const $ = (id) => document.getElementById(id);
const DOC = /^(research|docs)\/[A-Za-z0-9_\-./]+\.md$/;
const DATA = /^research\/[A-Za-z0-9_\-./]+\.(json|csv)$/;
const docLink = (path, label) => { const a = html('a', 'inline', label); a.href = './doc.html?p=' + path; return a; };
const GRADE = { '후보': 'stale', '표본내 점검': '', '탐색': '', '미달': 'error', '실시간 통과': 'ok' };
const clean = (path, pattern) => typeof path === 'string' && pattern.test(path) && !path.includes('..');

function drawRecord(card, registry) {
  const protocols = registry && Array.isArray(registry.protocols) ? registry.protocols : [];
  const fixed = protocols.filter((p) => p.status === 'fixed');
  const record = fixed.length ? ledgerSummary(card, fixed[0].id) : null;
  const tiles = $('rs-tiles'); tiles.replaceChildren();
  const add = (big, small) => { const d = html('div', 'tile'); d.append(html('b', '', big), html('span', '', small)); tiles.append(d); };
  if (record && record.sealed) {
    add(`${record.sealed}주`, `봉인된 판정 · ${record.first}부터 ${record.last}까지` + (record.on === null ? '' : ` · 기울임 ${record.on}주, 중립 ${record.off}주`));
    add(`${record.scored}주`, `채점된 판정 (${record.horizon}주 뒤에 채점)` + (record.difference === null ? '' : ` · 지금까지의 차이 ${fmt(record.difference, 2)}%p`));
    add(record.nextReview ? `채점 ${record.nextReview}주` : '끝남', record.nextReview ? '다음 점검 시점 · 그 전에는 판정하지 않는다' : '정해 둔 점검을 모두 지났다');
    $('rs-sealed').textContent = `${record.sealed}주`;
    $('rs-state').textContent = '기록 중'; $('rs-state').className = 'badge ok';
    $('rs-generated').textContent = card.generated_at ? kst(card.generated_at) : '—';
    const head = card.head && typeof card.head.records === 'string' ? card.head.records : null;
    $('rs-note').textContent = (head ? `기록의 마지막 줄 해시 ${head.slice(0, 16)}…  ` : '') + '채점은 봉인한 날보다 뒤의 첫 거래일 종가로 들어가 13주 뒤에 한다. 정해 둔 점검 시점 전에는 통과도 폐기도 선언하지 않는다.';
  } else {
    add('—', fixed.length ? '첫 봉인을 기다리고 있다' : '고정된 규약이 없어 봉인하지 않는다');
    $('rs-sealed').textContent = '0주';
    $('rs-state').textContent = fixed.length ? '대기' : '없음'; $('rs-state').className = 'badge';
    $('rs-note').textContent = '';
  }
  const table = $('rs-protocols'), head = html('tr');
  ['규약', '주장', '상태', '고정일', '문서'].forEach((h) => head.append(html('th', '', h)));
  const thead = html('thead'); thead.append(head); const body = html('tbody');
  protocols.forEach((p) => {
    const tr = html('tr'), name = html('td'), state = html('td'), link = html('td');
    name.append(html('span', 'rule-id', String(p.id)), document.createTextNode(p.title || ''));
    state.append(html('span', 'badge ' + (p.status === 'fixed' ? 'ok' : ''), p.status === 'fixed' ? '고정 · 기록 중' : '초안 · 봉인 안 함'));
    const path = 'research/protocols/' + p.document;
    if (clean(path, DOC)) link.append(docLink(path, '규약 →'));
    tr.append(name, html('td', 'wrap', p.claim || ''), state, html('td', '', typeof p.fixed_at === 'string' && validDate(p.fixed_at.slice(0, 10)) ? p.fixed_at.slice(0, 10) : '—'), link);
    body.append(tr);
  });
  table.replaceChildren(thead, body);
}

function drawStudies(index) {
  const host = $('rs-studies'); host.replaceChildren();
  (index.studies || []).forEach((s) => {
    const card = html('article', 'card'), top = html('div', 'card-top'), title = html('h3', '', s.title), links = html('p', 'card-links');
    top.append(title, html('span', 'badge ' + (GRADE[s.grade] || ''), s.grade), html('span', 'card-date', s.date));
    if (clean(s.readme, DOC)) links.append(docLink(s.readme, '결과 →'));
    if (clean(s.protocol, DOC)) links.append(docLink(s.protocol, '사전 설계 →'));
    (s.data || []).filter((p) => clean(p, DATA)).forEach((p) => { const a = html('a', 'inline', p.split('/').pop() + ' ↗'); a.href = './' + p; links.append(a); });
    card.append(top, html('p', 'card-q', s.question), html('p', 'card-a', s.finding), links);
    host.append(card);
  });
  const list = $('rs-archive'); list.replaceChildren();
  (index.archive || []).filter((a) => clean(a.path, DOC)).forEach((a) => { const li = html('li'); li.append(docLink(a.path, a.title)); list.append(li); });
}

async function start() {
  const [index, registry, card] = await Promise.all([get('./research/index.json').catch(() => null), get('./research/protocols/registry.json').catch(() => null), get('./ledger.json').catch(() => null)]);
  drawRecord(card, registry);
  if (index) drawStudies(index); else $('rs-studies').append(html('p', 'note', '연구 목록을 불러오지 못했습니다.'));
}
start();
})();
