/* Document viewer: shows the project's own Markdown files inside the site. Text is escaped before any markup is added. */
(() => {
'use strict';
const ALLOWED = /^(research|docs)\/[A-Za-z0-9_\-./]+\.md$/;

/* Only the project's own published documents: no scheme, no climbing out, no empty or dot segments. */
function safePath(path) {
  if (typeof path !== 'string' || !ALLOWED.test(path)) return null;
  return path.split('/').some((part) => part === '' || part === '.' || part === '..') ? null : path;
}

/* A relative link inside a document, as a path from the site root. Null when it leaves the site. */
function resolve(base, href) {
  const parts = base.split('/').slice(0, -1);
  for (const part of href.split('/')) {
    if (part === '' || part === '.') continue;
    if (part === '..') { if (!parts.length) return null; parts.pop(); } else parts.push(part);
  }
  return parts.join('/');
}

const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const slug = (s) => s.trim().toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '');

function link(text, href, base) {
  if (/^https?:\/\//i.test(href)) return `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${text} ↗</a>`;
  if (href.startsWith('#')) return `<a href="${esc(href)}">${text}</a>`;
  if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return text;                       // any other scheme is dropped
  const [file, fragment] = href.split('#');
  const target = resolve(base, file);
  if (target && safePath(target)) return `<a href="./doc.html?p=${esc(target)}${fragment ? '#' + esc(fragment) : ''}">${text}</a>`;
  if (target && /^(research|docs)\/[A-Za-z0-9_\-./]+\.(json|csv)$/.test(target)) return `<a href="./${esc(target)}">${text} ↗</a>`;
  return text;                                                              // a file the site does not publish
}

function inline(source, base) {
  // code spans first, so nothing inside them is read as markup
  return source.split(/(`[^`]*`)/).map((piece) => {
    if (piece.length > 1 && piece.startsWith('`') && piece.endsWith('`')) return `<code>${esc(piece.slice(1, -1))}</code>`;
    let out = esc(piece);
    out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt) => alt);
    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, text, href) => link(text, href.replace(/&amp;/g, '&'), base));
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/(^|[\s(])\*([^*\s][^*]*)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
    return out;
  }).join('');
}

const isRule = (line) => /^ {0,3}([-*_])( ?\1){2,} *$/.test(line);
const listItem = (line) => line.match(/^( *)([-*+]|\d+[.)]) +(.*)$/);
const tableRow = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
const isDivider = (line) => /^ *\|? *:?-{2,}:? *(\| *:?-{2,}:? *)*\|? *$/.test(line) && line.includes('-');

function render(markdown, base = 'docs/x.md') {
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let i = 0;
  const startsBlock = (line) => /^ {0,3}#{1,6} /.test(line) || /^ {0,3}(```|~~~)/.test(line) || /^ {0,3}>/.test(line) || isRule(line) || !!listItem(line);
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    const fence = line.match(/^ {0,3}(```|~~~)/);
    if (fence) {
      const body = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) { body.push(lines[i]); i += 1; }
      i += 1;
      out.push(`<pre><code>${esc(body.join('\n'))}</code></pre>`);
      continue;
    }
    const heading = line.match(/^ {0,3}(#{1,6}) +(.*?) *#* *$/);
    if (heading) {
      const level = heading[1].length;
      out.push(`<h${level} id="${esc(slug(heading[2]))}">${inline(heading[2], base)}</h${level}>`);
      i += 1;
      continue;
    }
    if (isRule(line)) { out.push('<hr>'); i += 1; continue; }
    if (/^ {0,3}>/.test(line)) {
      const body = [];
      while (i < lines.length && /^ {0,3}>/.test(lines[i])) { body.push(lines[i].replace(/^ {0,3}> ?/, '')); i += 1; }
      out.push(`<blockquote>${render(body.join('\n'), base)}</blockquote>`);
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && isDivider(lines[i + 1])) {
      const head = tableRow(line), align = tableRow(lines[i + 1]).map((c) => (c.endsWith(':') ? (c.startsWith(':') ? 'center' : 'right') : ''));
      const cell = (tag, text, k) => `<${tag}${align[k] ? ` style="text-align:${align[k]}"` : ''}>${inline(text, base)}</${tag}>`;
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) { rows.push(tableRow(lines[i])); i += 1; }
      out.push(`<div class="doc-table"><table><thead><tr>${head.map((t, k) => cell('th', t, k)).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${head.map((_, k) => cell('td', r[k] || '', k)).join('')}</tr>`).join('')}</tbody></table></div>`);
      continue;
    }
    const first = listItem(line);
    if (first) {
      // one list; deeper-indented items become a nested list inside the item above them
      const ordered = /\d/.test(first[2]), indent = first[1].length, items = [];
      while (i < lines.length) {
        const item = listItem(lines[i]);
        if (item && item[1].length <= indent + 1) {
          if (/\d/.test(item[2]) !== ordered) break;
          items.push({ text: item[3], child: [] }); i += 1;
        } else if (item) { items[items.length - 1].child.push(lines[i].slice(indent + 2)); i += 1;
        } else if (lines[i].trim() && /^ {2,}/.test(lines[i]) && items.length) { items[items.length - 1].text += ' ' + lines[i].trim(); i += 1;
        } else break;
      }
      const tag = ordered ? 'ol' : 'ul';
      out.push(`<${tag}>${items.map((it) => `<li>${inline(it.text, base)}${it.child.length ? render(it.child.join('\n'), base) : ''}</li>`).join('')}</${tag}>`);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !(para.length && startsBlock(lines[i])) && !(lines[i].includes('|') && i + 1 < lines.length && isDivider(lines[i + 1]))) { para.push(lines[i].trim()); i += 1; }
    out.push(`<p>${inline(para.join(' '), base)}</p>`);
  }
  return out.join('\n');
}

if (typeof module !== 'undefined') module.exports = { render, safePath, resolve, inline };
if (typeof document === 'undefined') return;

const $ = (id) => document.getElementById(id);
function fail(message) { $('doc-banner').textContent = message; $('doc-banner').hidden = false; $('doc').hidden = true; }

async function start() {
  const path = safePath(new URLSearchParams(location.search).get('p'));
  if (!path) return fail('열 수 있는 문서가 아닙니다. 연구와 검증 기록에서 문서를 골라 주세요.');
  $('doc-path').textContent = path.replace(/^research\/studies\//, '연구 › ').replace(/^research\/protocols\//, '검증 규약 › ').replace(/^research\/archive\//, '보관 › ').replace(/^docs\//, '자료 원천 › ').replace(/\//g, ' › ');
  let text;
  try {
    const response = await fetch('./' + path + '?t=' + Date.now(), { cache: 'no-store', credentials: 'omit' });
    if (!response.ok) throw Error(String(response.status));
    text = await response.text();
  } catch (error) { return fail('문서를 불러오지 못했습니다. 주소가 바뀌었거나 아직 게시되지 않은 문서입니다.'); }
  const body = $('doc');
  body.innerHTML = render(text, path);                      // every piece of text was escaped in render()
  const title = body.querySelector('h1');
  if (title) { $('title').textContent = title.textContent; document.title = title.textContent + ' · 유동성 측정기'; title.remove(); }
  // An archived report can point at a data file this site does not carry. Ask before leaving a link that leads nowhere.
  body.querySelectorAll('a[href^="./research/"],a[href^="./docs/"]').forEach(async (a) => {
    try { if ((await fetch(a.getAttribute('href'), { method: 'HEAD', cache: 'no-store', credentials: 'omit' })).ok) return; } catch (error) { /* treated as missing */ }
    const plain = document.createElement('span'); plain.className = 'missing';
    plain.textContent = a.textContent.replace(/ ↗$/, '') + ' (이 사이트에 싣지 않은 파일)'; a.replaceWith(plain);
  });
  const raw = document.createElement('a'); raw.className = 'inline'; raw.href = './' + path; raw.textContent = '원문(Markdown) ↗';
  $('doc-source').replaceChildren(raw);
  if (location.hash) { const target = document.getElementById(decodeURIComponent(location.hash.slice(1))); if (target) target.scrollIntoView(); }
}
start();
})();
