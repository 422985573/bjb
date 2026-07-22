/* 虚拟小包报价表 — 前端：展示价格表 + 月度参数 + 多邮编报价计算 */
(function () {
  'use strict';

  var API_INDEX = '/api/xiaobao-sheets';
  var API_SHEET = '/api/xiaobao-sheet/';
  var API_ZONE_LOOKUP = '/api/xiaobao-zone-lookup';
  var API_SETTINGS = '/api/xiaobao-settings';

  // 需要暂时隐藏的仓库（按标题关键词匹配）；日后恢复展示：清空此数组即可
  var HIDDEN_WAREHOUSE_KEYWORDS = ['墨尔本'];

  var MAX_CODES = 50;

  var sheetsIndex = [];
  var currentKey = '';
  var currentData = null;
  var settingsData = {};
  var selectedMonth = (new Date().getMonth() + 1);
  // 搜索态：{ codes:[...], zoneByCode:{code:{found,zone,suburb,state}}, weight:Number }
  var searchState = null;

  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  function $$(sel, ctx) { return Array.from((ctx || document).querySelectorAll(sel)); }

  function esc(v) {
    if (v === null || v === undefined) return '';
    var d = document.createElement('div');
    d.textContent = String(v);
    return d.innerHTML;
  }
  function fmt(n) {
    if (n === null || n === undefined || isNaN(n)) return '';
    return (Math.round(n * 100) / 100).toString();
  }

  function cleanRichtextHtml(html) {
    if (!html) return '';
    var prev;
    do {
      prev = html;
      html = html.replace(/<p[^>]*>(?:\s|&nbsp;|&#160;|&#xA0;|<br\s*\/?>)*<\/p>/gi, '');
    } while (html !== prev);
    return html;
  }
  function brToParagraphs(html) {
    if (!html) return '';
    if (/<p[\s>]/i.test(html)) return html;
    var parts = html.split(/<br\s*\/?>/i);
    var out = parts.map(function (p) {
      p = p.replace(/^\s+|\s+$/g, '');
      return p ? '<p>' + p + '</p>' : '';
    }).filter(Boolean).join('');
    return out || html;
  }

  // ---- table render helpers ----
  function cellValue(c) {
    if (c && typeof c === 'object' && !Array.isArray(c)) {
      return c.v != null ? c.v : (c.text != null ? c.text : '');
    }
    return c;
  }
  function cellColspan(c) {
    if (c && typeof c === 'object' && !Array.isArray(c)) { return c.cs || c.colspan || 1; }
    return 1;
  }
  function buildColspanCover(rows) {
    var covered = {};
    for (var ri = 0; ri < rows.length; ri++) {
      for (var ci = 0; ci < rows[ri].length; ci++) {
        var cs = cellColspan(rows[ri][ci]);
        if (cs > 1) { for (var k = 1; k < cs; k++) covered[ri + '_' + (ci + k)] = true; }
      }
    }
    return covered;
  }
  function isFullSpanRow(row, colCount) {
    if (colCount < 5) return false;
    var filled = 0, leadingEmpty = 0, seenFilled = false;
    for (var i = 0; i < row.length; i++) {
      var v = cellValue(row[i]);
      if (v !== '' && v !== null && v !== undefined) { seenFilled = true; filled += cellColspan(row[i]); }
      else if (!seenFilled) { leadingEmpty++; }
    }
    if (leadingEmpty > 1) return false;
    return filled > 0 && filled * 3 <= colCount;
  }
  function buildRowspanMap(rows) {
    var map = {};
    if (!rows.length) return map;
    var cols = rows[0].length;
    var covered = buildColspanCover(rows);
    for (var ci = 0; ci < cols; ci++) {
      var hasGrouping = false;
      for (var ri = 1; ri < rows.length; ri++) {
        if (covered[ri + '_' + ci]) continue;
        var v = cellValue(rows[ri][ci]);
        if (v === '' || v === null || v === undefined) {
          var prevV = cellValue(rows[ri - 1][ci]);
          if (prevV !== '' && prevV !== null && prevV !== undefined) { hasGrouping = true; break; }
        }
      }
      if (!hasGrouping) continue;
      var anchor = -1;
      for (var rj = 0; rj < rows.length; rj++) {
        if (covered[rj + '_' + ci]) {
          if (anchor >= 0) map[anchor + '_' + ci] = rj - anchor;
          anchor = -1; continue;
        }
        var vv = cellValue(rows[rj][ci]);
        var isEmpty = (vv === '' || vv === null || vv === undefined);
        if (isFullSpanRow(rows[rj], cols)) {
          if (anchor >= 0) map[anchor + '_' + ci] = rj - anchor;
          anchor = -1; continue;
        }
        if (!isEmpty) {
          if (anchor >= 0) map[anchor + '_' + ci] = rj - anchor;
          anchor = rj; map[rj + '_' + ci] = 1;
        } else {
          if (anchor >= 0) map[rj + '_' + ci] = 0;
        }
      }
      if (anchor >= 0) map[anchor + '_' + ci] = rows.length - anchor;
    }
    return map;
  }
  function renderTableSection(sec) {
    var rows = sec.rows || [];
    var headers = sec.headers || [];
    if (!rows.length && !headers.length) return '';
    var colCount = rows.length ? rows[0].length : headers.length;
    var mergeMap = buildRowspanMap(rows);
    var h = '<div class="wh-table-wrap"><table class="wh-table"><thead><tr>';
    headers.forEach(function (th) { h += '<th>' + esc(th) + '</th>'; });
    h += '</tr></thead><tbody>';
    rows.forEach(function (row, ri) {
      if (isFullSpanRow(row, colCount)) {
        var parts = [];
        row.forEach(function (c) { var v = cellValue(c); if (v !== '' && v !== null && v !== undefined) parts.push(String(v)); });
        h += '<tr><td colspan="' + colCount + '" class="wh-cell-note">' + esc(parts.join('  ')) + '</td></tr>';
      } else {
        h += '<tr>';
        var ci = 0;
        while (ci < row.length) {
          var m = mergeMap[ri + '_' + ci];
          if (m === 0) { ci++; continue; }
          var value = cellValue(row[ci]);
          var cs = cellColspan(row[ci]);
          var cls = 'wh-col-' + ci;
          if (typeof value === 'string' && value.length > 30) cls += ' wh-cell-note';
          var rs = m > 1 ? ' rowspan="' + m + '"' : '';
          var csAttr = cs > 1 ? ' colspan="' + cs + '"' : '';
          h += '<td' + rs + csAttr + ' class="' + cls + '">' + esc(value) + '</td>';
          ci += cs;
        }
        h += '</tr>';
      }
    });
    h += '</tbody></table></div>';
    return h;
  }

  // 命中分区行：扁平表，仅展示匹配行并全部高亮
  function renderFilteredTable(sec, zones) {
    var rows = sec.rows || [];
    var headers = sec.headers || [];
    var zcol = zoneColOf(headers);
    var matchers = zones.map(makeZoneMatcher);
    var matched = rows.filter(function (row) {
      var code = String(cellValue(row[zcol]) == null ? '' : cellValue(row[zcol])).trim();
      return matchers.some(function (mt) { return mt(code); });
    });
    var h = '<div class="wh-table-wrap"><table class="wh-table"><thead><tr>';
    headers.forEach(function (th) { h += '<th>' + esc(th) + '</th>'; });
    h += '</tr></thead><tbody>';
    if (!matched.length) {
      h += '<tr><td colspan="' + headers.length + '" class="wh-cell-note">无匹配分区的报价行</td></tr>';
    } else {
      matched.forEach(function (row) {
        h += '<tr class="wh-row-highlight">';
        for (var ci = 0; ci < headers.length; ci++) { h += '<td>' + esc(cellValue(row[ci])) + '</td>'; }
        h += '</tr>';
      });
    }
    h += '</tbody></table></div>';
    return h;
  }

  function zoneColOf(headers) {
    for (var i = 0; i < headers.length; i++) {
      if (/zone/i.test(String(headers[i] || ''))) return i;
    }
    return 1;
  }

  // 分区名匹配：精确 或 「分区名 + 尾随数字」
  function makeZoneMatcher(zone) {
    var z = String(zone == null ? '' : zone).trim();
    if (!z) return function () { return false; };
    var reSuffix = new RegExp('^' + z.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\d+$');
    return function (cellText) {
      var t = cellText == null ? '' : String(cellText).trim();
      return t === z || reSuffix.test(t);
    };
  }

  // 解析重量表头为 kg 上限；非重量列返回 null
  function parseWeightHeader(h) {
    var s = String(h == null ? '' : h).toLowerCase().trim();
    if (!s) return null;
    var nums = s.match(/[0-9]+(?:\.[0-9]+)?/g);
    if (!nums) return null;
    if (/kg/.test(s)) return Math.max.apply(null, nums.map(Number));
    if (/g/.test(s)) return Number(nums[0]) / 1000;
    return null;
  }

  // 按重量在价格行里取尾程派送费用(AUD)
  function pickDeliver(headers, row, weight) {
    var brackets = [];
    var baseIdx = -1, kiloIdx = -1, maxBound = 0;
    for (var ci = 0; ci < headers.length; ci++) {
      var hs = String(headers[ci] || '').trim();
      if (/^base$/i.test(hs)) { baseIdx = ci; continue; }
      if (/^kilo$/i.test(hs)) { kiloIdx = ci; continue; }
      var b = parseWeightHeader(hs);
      if (b != null) { brackets.push({ ci: ci, bound: b, label: hs }); if (b > maxBound) maxBound = b; }
    }
    brackets.sort(function (a, b) { return a.bound - b.bound; });
    for (var i = 0; i < brackets.length; i++) {
      if (brackets[i].bound >= weight) {
        var v = Number(cellValue(row[brackets[i].ci]));
        if (!isNaN(v) && cellValue(row[brackets[i].ci]) !== '') {
          return { label: brackets[i].label, aud: v };
        }
      }
    }
    // 超出最大档：Base + Kilo × 进位重量
    if (baseIdx >= 0 && kiloIdx >= 0) {
      var base = Number(cellValue(row[baseIdx]));
      var kilo = Number(cellValue(row[kiloIdx]));
      if (!isNaN(base) && !isNaN(kilo)) {
        return { label: '>' + maxBound + 'KG', aud: base + kilo * Math.ceil(weight) };
      }
    }
    return null;
  }

  // ---- init / load ----
  function init() {
    fetchSettings();
    fetch(API_INDEX)
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.success || !res.data || !res.data.length) {
          $('#whNavList').innerHTML = '<li class="wh-nav-item wh-nav-loading">暂无数据</li>';
          return;
        }
        sheetsIndex = res.data;
        loadSheet(sheetsIndex[0].key);
      })
      .catch(function () { $('#whNavList').innerHTML = '<li class="wh-nav-item wh-nav-loading">加载失败</li>'; });
    bindPostcodeBoxes();
    bindTocPanel();
  }

  function fetchSettings() {
    fetch(API_SETTINGS).then(function (r) { return r.json(); }).then(function (res) {
      if (res.success) { settingsData = res.data || {}; if (currentData) renderContent(); }
    }).catch(function () {});
  }

  function loadSheet(key) {
    currentKey = key;
    var content = $('#whContent');
    content.innerHTML = '<div class="wh-loading"><div class="wh-spinner"></div><span>加载中...</span></div>';
    fetch(API_SHEET + encodeURIComponent(key))
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.success) { content.innerHTML = '<p style="color:#ef4444;padding:20px;">加载失败</p>'; return; }
        currentData = res.data;
        renderContent();
      })
      .catch(function () { content.innerHTML = '<p style="color:#ef4444;padding:20px;">网络错误</p>'; });
  }

  function curSettings() {
    return settingsData[String(selectedMonth)] || { unit_price: 0, exchange_rate: 0, fuel_rate: 0 };
  }

  function visiblePriceTables() {
    if (!currentData) return [];
    return (currentData.sections || []).filter(function (s) { return s.type === 'price_table'; })
      .filter(function (s) {
        var t = String(s.title || '');
        return !HIDDEN_WAREHOUSE_KEYWORDS.some(function (kw) { return t.indexOf(kw) >= 0; });
      });
  }

  // 计算某价格表下命中邮编的报价明细（供结果表与导出复用）
  function computeQuotes(sec) {
    var s = curSettings();
    var headers = sec.headers || [];
    var rows = sec.rows || [];
    var zcol = zoneColOf(headers);
    var weight = searchState ? searchState.weight : 0;
    if (!searchState || !weight || weight <= 0) return { rows: [], sum: 0 };

    var unitPrice = Number(s.unit_price) || 0;
    var exRate = Number(s.exchange_rate) || 0;
    var fuelMult = 1 + (Number(s.fuel_rate) || 0) / 100;
    var fuelMultR = Math.round(fuelMult * 10000) / 10000;

    var out = [];
    var sum = 0;
    searchState.codes.forEach(function (code) {
      var info = searchState.zoneByCode[code];
      if (!info || !info.found) return;
      var mt = makeZoneMatcher(info.zone);
      var row = null;
      for (var i = 0; i < rows.length; i++) {
        var cc = String(cellValue(rows[i][zcol]) == null ? '' : cellValue(rows[i][zcol])).trim();
        if (mt(cc)) { row = rows[i]; break; }
      }
      if (!row) return;
      var deliver = pickDeliver(headers, row, weight);
      if (!deliver) return;
      var head = unitPrice * weight;
      var tail = deliver.aud * fuelMult * exRate;
      var total = head + tail;
      sum += total;
      out.push({
        code: code, zone: info.zone, label: deliver.label,
        headF: unitPrice + '×' + weight,
        tailF: fmt(deliver.aud) + '×' + fuelMultR + '×' + exRate,
        total: total
      });
    });
    return { rows: out, sum: sum };
  }

  function settingsBlockHtml() {
    var s = curSettings();
    var h = '<div class="xb-settings">';
    h += '<div class="xb-settings-vals">';
    h += '<div>' + selectedMonth + '月份头程运输费用单价：<b>' + esc(s.unit_price) + '</b> 元/kg</div>';
    h += '<div>' + selectedMonth + '月份澳洲邮政燃油费率：<b>' + esc(s.fuel_rate) + '</b> %</div>';
    h += '<div>' + selectedMonth + '月份澳币换算人民币汇率：<b>' + esc(s.exchange_rate) + '</b></div>';
    h += '</div></div>';
    return h;
  }

  function renderContent() {
    if (!currentData) return;
    var sections = currentData.sections || [];
    var richtextTop = sections.filter(function (s) { return s.type === 'richtext'; });
    var priceTables = visiblePriceTables();

    var html = '<h2 class="wh-sheet-title">' + esc(currentData.name) + '</h2>';
    richtextTop.forEach(function (sec) {
      html += '<div class="wh-section xb-note-box">';
      if (sec.title) html += '<div class="wh-section-title">' + esc(sec.title) + '</div>';
      html += '<div class="wh-richtext">' + cleanRichtextHtml(brToParagraphs(sec.html || '')) + '</div></div>';
    });

    html += settingsBlockHtml();

    var zones = searchState ? Object.keys(searchState.zoneByCode).map(function (c) {
      return searchState.zoneByCode[c].found ? searchState.zoneByCode[c].zone : null;
    }).filter(Boolean) : [];

    priceTables.forEach(function (sec, pi) {
      html += '<div class="wh-section" id="xbSec' + pi + '">';
      if (sec.title) html += '<div class="wh-section-title">' + esc(sec.title) + '</div>';
      if (searchState) {
        html += renderFilteredTable(sec, zones);
      } else {
        html += renderTableSection(sec);
      }
      html += '</div>';
      if (searchState) html += resultTableHtml(sec);
    });

    $('#whContent').innerHTML = html;

    renderNav(priceTables);
  }

  // 计算某仓库报价结果表
  function resultTableHtml(sec) {
    var whName = navLabel(sec, 0);
    var weight = searchState ? searchState.weight : 0;

    var h = '<div class="xb-result">';
    h += '<div class="xb-result-title">最终全程运费总价（' + esc(whName) + '）</div>';

    if (!weight || weight <= 0) {
      h += '<div class="xb-result-empty">请输入重量(kg)后计算价格</div></div>';
      return h;
    }

    var q = computeQuotes(sec);
    var out = q.rows;
    if (!out.length) {
      h += '<div class="xb-result-empty">未找到可计算的报价（邮编未命中分区或该重量无对应价格）</div></div>';
      return h;
    }

    h += '<table><thead><tr><th>邮编</th><th>公斤段</th><th>头程计算公式</th><th>尾程计算公式</th><th>总价(元)</th></tr></thead><tbody>';
    out.forEach(function (r) {
      h += '<tr><td>' + esc(r.code) + '</td><td>' + esc(r.label) + '</td><td>' + esc(r.headF) + '</td><td>' + esc(r.tailF) + '</td><td class="xb-total">' + fmt(r.total) + '</td></tr>';
    });
    h += '<tr class="xb-sum-row"><td colspan="4">合计</td><td>' + fmt(q.sum) + '</td></tr>';
    h += '</tbody></table>';
    h += '<div class="xb-result-note">备注：选择服务则代表已完整阅读渠道说明和费用详解。</div>';
    h += '</div>';
    return h;
  }

  function renderNav(priceTables) {
    var ul = $('#whNavList');
    var tocList = $('#whTocList');
    if (ul) ul.innerHTML = '';
    if (tocList) tocList.innerHTML = '';
    priceTables.forEach(function (sec, si) {
      var label = navLabel(sec, si);
      if (ul) {
        var li = document.createElement('li');
        li.className = 'wh-nav-item';
        li.textContent = label;
        li.onclick = function () { scrollToSection(si); };
        ul.appendChild(li);
      }
      if (tocList) {
        var tli = document.createElement('li');
        tli.textContent = label;
        tli.onclick = function () { scrollToSection(si); closeTocPanel(); };
        tocList.appendChild(tli);
      }
    });
  }

  function navLabel(sec, si) {
    var title = sec.title || ('区块 ' + (si + 1));
    var m = title.match(/[（(]([^）)]+)[）)]/);
    return m ? m[1].trim() : title;
  }

  function scrollToSection(si) {
    var el = document.getElementById('xbSec' + si);
    if (!el) return;
    if (window.innerWidth > 768) {
      var content = $('#whContent');
      content.scrollTo({ top: el.offsetTop - 10, behavior: 'smooth' });
    } else {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  // ---- 查询 ----
  function parseCodes(raw) {
    var tokens = String(raw || '').split(/[\s,，、;；]+/);
    var seen = {}, codes = [];
    tokens.forEach(function (t) {
      var digits = t.replace(/[^0-9]/g, '');
      if (!digits || digits.length > 4) return;
      var pc = ('0000' + digits).slice(-4);
      if (!seen[pc]) { seen[pc] = 1; codes.push(pc); }
    });
    return codes.slice(0, MAX_CODES);
  }

  // 统计已输入邮编数（去重、仅4位内数字）
  function countCodes(raw) {
    var tokens = String(raw || '').split(/[\s,，、;；]+/);
    var seen = {};
    tokens.forEach(function (t) {
      var digits = t.replace(/[^0-9]/g, '');
      if (!digits || digits.length > 4) return;
      seen[('0000' + digits).slice(-4)] = 1;
    });
    return Object.keys(seen).length;
  }

  function updateTip(raw) {
    var tip = $('#xbPcTip');
    if (!tip) return;
    var n = countCodes(raw);
    if (n === 0) {
      tip.textContent = '最多可批量查询 ' + MAX_CODES + ' 个邮编';
      tip.className = 'xb-hero-tip';
    } else if (n > MAX_CODES) {
      tip.textContent = '已输入 ' + n + ' 个，超出上限，仅查询前 ' + MAX_CODES + ' 个邮编';
      tip.className = 'xb-hero-tip warn';
    } else {
      tip.textContent = '已输入 ' + n + ' / ' + MAX_CODES + ' 个邮编';
      tip.className = 'xb-hero-tip';
    }
  }

  function runLookup(rawCodes, weight, summaryEl) {
    var codes = parseCodes(rawCodes);
    if (!codes.length) {
      searchState = null;
      if (summaryEl) summaryEl.innerHTML = '';
      renderContent();
      return;
    }
    if (summaryEl) summaryEl.innerHTML = '<div class="xb-pc-hint">查询中...</div>';
    fetch(API_ZONE_LOOKUP + '?codes=' + encodeURIComponent(codes.join(',')))
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.success) { if (summaryEl) summaryEl.innerHTML = '<div class="xb-pc-hint">查询失败</div>'; return; }
        var zoneByCode = {};
        (res.data || []).forEach(function (it) { zoneByCode[it.code] = it; });
        searchState = { codes: codes, zoneByCode: zoneByCode, weight: (Number(weight) || 0) };
        renderSummary(res.data || [], summaryEl);
        renderContent();
      })
      .catch(function () { if (summaryEl) summaryEl.innerHTML = '<div class="xb-pc-hint">网络错误</div>'; });
  }

  function renderSummary(data, summaryEl) {
    if (!summaryEl) return;
    if (!data.length) { summaryEl.innerHTML = ''; return; }
    var h = '';
    data.forEach(function (it) {
      if (it.found) {
        var loc = [it.suburb, it.state].filter(Boolean).join(', ');
        h += '<div class="xb-pc-item"><span class="xb-pc-code">' + esc(it.code) + '</span>';
        h += '<span class="xb-pc-zone">' + esc(it.zone) + '</span>';
        if (loc) h += '<span class="xb-pc-loc">' + esc(loc) + '</span>';
        h += '</div>';
      } else {
        h += '<div class="xb-pc-item miss"><span class="xb-pc-code">' + esc(it.code) + '</span>';
        h += '<span class="xb-pc-zone">未找到</span></div>';
      }
    });
    summaryEl.innerHTML = h;
  }

  function bindPostcodeBoxes() {
    var input = $('#xbPcInput');
    var weightEl = $('#xbWeight');
    var queryBtn = $('#xbPcQuery');
    var clearBtn = $('#xbClear');
    var exportBtn = $('#xbExport');
    var summaryEl = $('#xbPcSummary');
    if (!input) return;

    function doQuery() { runLookup(input.value, weightEl ? weightEl.value : 0, summaryEl); }
    if (queryBtn) queryBtn.addEventListener('click', doQuery);
    if (clearBtn) clearBtn.addEventListener('click', function () {
      input.value = '';
      if (weightEl) weightEl.value = '';
      if (summaryEl) summaryEl.innerHTML = '';
      searchState = null;
      updateTip('');
      renderContent();
      input.focus();
    });
    if (exportBtn) exportBtn.addEventListener('click', exportResults);
    input.addEventListener('input', function () { updateTip(input.value); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); doQuery(); }
    });
    if (weightEl) weightEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); doQuery(); }
    });
  }

  // 导出当前报价结果为 CSV
  function exportResults() {
    if (!searchState || !searchState.weight) { alert('请先输入邮编和重量并查询报价'); return; }
    var tables = visiblePriceTables();
    var lines = [];
    var any = false;
    tables.forEach(function (sec) {
      var q = computeQuotes(sec);
      if (!q.rows.length) return;
      any = true;
      lines.push([navLabel(sec, 0)]);
      lines.push(['邮编', '分区', '公斤段', '头程计算公式', '尾程计算公式', '总价(元)']);
      q.rows.forEach(function (r) {
        lines.push([r.code, r.zone, r.label, r.headF, r.tailF, fmt(r.total)]);
      });
      lines.push(['合计', '', '', '', '', fmt(q.sum)]);
      lines.push([]);
    });
    if (!any) { alert('当前没有可导出的报价结果'); return; }

    var csv = lines.map(function (row) {
      return row.map(function (c) {
        var s = (c == null ? '' : String(c));
        if (/[",\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
        return s;
      }).join(',');
    }).join('\r\n');

    var blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = '虚拟小包报价.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  // ---- mobile TOC panel ----
  function closeTocPanel() {
    var overlay = $('#whTocOverlay');
    var panel = $('#whTocPanel');
    if (overlay) overlay.classList.remove('open');
    if (panel) panel.classList.remove('open');
  }
  function bindTocPanel() {
    var fab = $('#whTocFab');
    var overlay = $('#whTocOverlay');
    var panel = $('#whTocPanel');
    var closeBtn = $('#whTocClose');
    if (!fab) return;
    fab.onclick = function () { overlay.classList.add('open'); panel.classList.add('open'); };
    if (overlay) overlay.onclick = closeTocPanel;
    if (closeBtn) closeBtn.onclick = closeTocPanel;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
