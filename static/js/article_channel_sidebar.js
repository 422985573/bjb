/* 新大货快递报价表：渠道表 + 4 张海外仓小价格表 同页展示，统一按邮编搜索。
 *
 * - 渠道搜索复用 article.js 的全局函数（isPostcodeInRange / highlightMatchedRowsWithMergedCells /
 *   mergeAllChannelTables / unmergeAllChannelTables / _syncChannelNavWithSearch）。
 * - 海外仓表：页面加载并行拉 4 张 slim detail（剔除上万行分区表，仅 price_table+richtext+postcode_zone_map），
 *   自带精简表格渲染；搜索时按 postcode_zone_map[邮编]→分区→高亮 price_table 里 Zone 匹配行。
 * - 顶部单查 / 左侧批量查 统一走「渠道 + 海外仓」两路并行。
 */
(function () {
  'use strict';

  var MAX_CODES = 50;
  var WH_KEYS = ['allied', 'border', 'tfm', 'toll'];
  var whMaps = {};   // { key: postcode_zone_map }
  var whData = {};   // { key: 完整 sheet data（含 sections，用于运费总价计算） }
  var whReady = {};  // { key: true } 渲染完成
  var whSettings = { fuel_rate: 20, gst_rate: 10 };  // 海外仓运费参数（后台可配燃油率；GST 固定 10%）

  function $(id) { return document.getElementById(id); }

  function esc(v) {
    if (v === null || v === undefined) return '';
    var d = document.createElement('div');
    d.textContent = String(v);
    return d.innerHTML;
  }

  // ---- 邮编解析 ----
  function parseCodes(raw) {
    var tokens = String(raw || '').split(/[\s,，、;；]+/);
    var seen = {}, codes = [];
    tokens.forEach(function (t) {
      var digits = t.replace(/[^0-9]/g, '');
      if (!digits || digits.length !== 4) return;
      if (!seen[digits]) { seen[digits] = 1; codes.push(digits); }
    });
    return codes.slice(0, MAX_CODES);
  }
  function oneCode(raw) {
    var digits = String(raw || '').replace(/[^0-9]/g, '').slice(0, 4);
    return digits.length === 4 ? digits : '';
  }

  // ============================================================
  // 海外仓：精简渲染 + 搜索
  // ============================================================

  // 分区匹配（照搬 article_warehouse.js makeZoneMatcher 语义：精确 或 zone+尾随数字）
  function makeZoneMatcher(zone) {
    var z = String(zone == null ? '' : zone).trim();
    if (!z) return function () { return false; };
    var escaped = z.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var re = new RegExp('^' + escaped + '\\d*$');
    return function (cell) {
      var c = String(cell == null ? '' : cell).trim();
      return c === z || re.test(c);
    };
  }

  function whRichtextToHtml(html) {
    if (!html) return '';
    // 去空段，粗略保留结构
    return String(html).replace(/<p[^>]*>(?:\s|&nbsp;|<br\s*\/?>)*<\/p>/gi, '');
  }

  // 渲染单个 price_table section 为 HTML（含合并单元格无关，海外仓表简单，逐行输出）
  function renderWhTable(sec) {
    var rows = sec.rows || [];
    var headers = sec.headers || [];
    if (!rows.length && !headers.length) return '';
    var h = '<div class="wh-table-wrap"><table class="wh-table"><thead><tr>';
    headers.forEach(function (th) { h += '<th>' + esc(th) + '</th>'; });
    h += '</tr></thead><tbody>';
    rows.forEach(function (row) {
      // 州名分组行（只有首格有值，其余空）：整行合并展示
      var filled = row.filter(function (c) { return c !== '' && c !== null && c !== undefined; });
      if (row.length >= 3 && filled.length === 1 && (row[0] !== '' && row[0] != null)) {
        h += '<tr class="wh-group-row"><td colspan="' + row.length + '">' + esc(row[0]) + '</td></tr>';
        return;
      }
      h += '<tr>';
      row.forEach(function (c) { h += '<td>' + esc(c) + '</td>'; });
      h += '</tr>';
    });
    h += '</tbody></table></div>';
    return h;
  }

  function renderWhCard(key, data) {
    var card = $('wh-card-' + key);
    if (!card) return;
    var name = (data && data.name) || key;
    var html = '<h2 class="wh-card-title">' + esc(name) + '</h2>';
    html += '<div class="wh-card-result" id="wh-result-' + key + '"></div>';
    (data.sections || []).forEach(function (sec) {
      if (sec.type === 'price_table') {
        if (sec.title) html += '<div class="wh-card-section-title">' + esc(sec.title) + '</div>';
        html += renderWhTable(sec);
      } else if (sec.type === 'richtext') {
        if (sec.title) html += '<div class="wh-card-section-title">' + esc(sec.title) + '</div>';
        html += '<div class="wh-richtext">' + whRichtextToHtml(sec.html || '') + '</div>';
      }
    });
    card.innerHTML = html;
  }

  function loadWarehouseTables() {
    var inline = $('whInline');
    var dir = (inline && inline.getAttribute('data-wh-dir')) || 'warehouse_au_dahuo';
    WH_KEYS.forEach(function (key) {
      var url = '/api/warehouse-sheet/' + encodeURIComponent(key) + '?dir=' + encodeURIComponent(dir) + '&slim=1';
      fetch(url).then(function (r) { return r.json(); }).then(function (res) {
        if (!res || !res.success || !res.data) {
          var card = $('wh-card-' + key);
          if (card) card.innerHTML = '<div class="wh-card-error">加载失败</div>';
          return;
        }
        whMaps[key] = res.data.postcode_zone_map || {};
        whData[key] = res.data;
        renderWhCard(key, res.data);
        whReady[key] = true;
        // 若加载完成时搜索框已有值，补跑一次（竞态兜底）
        var topVal = oneCode(($('globalPostcodeInput') || {}).value);
        var batchVal = parseCodes(($('batchPcInput') || {}).value);
        if (topVal) searchWarehouseInlineOne(key, [topVal]);
        else if (batchVal.length) searchWarehouseInlineOne(key, batchVal);
      }).catch(function () {
        var card = $('wh-card-' + key);
        if (card) card.innerHTML = '<div class="wh-card-error">网络错误</div>';
      });
    });
  }

  // 对单张海外仓卡按邮编集合查询/高亮；返回是否命中
  function searchWarehouseInlineOne(key, codes) {
    var card = $('wh-card-' + key);
    if (!card || !whMaps[key]) return false;
    var map = whMaps[key];
    // 清高亮
    card.querySelectorAll('.wh-table tbody tr.wh-hit').forEach(function (tr) { tr.classList.remove('wh-hit'); });
    var resultEl = $('wh-result-' + key);

    if (!codes || !codes.length) {
      card.classList.remove('hidden-by-search');
      if (resultEl) resultEl.textContent = '';
      var q0 = $('wh-quote-' + key);
      if (q0) q0.innerHTML = '';
      return true;
    }

    // 收集命中的分区代码（可能多个邮编 → 多个分区）
    var zones = [], zoneLabels = [], missCodes = [];
    codes.forEach(function (code) {
      var z = map[code];
      if (z) { if (zones.indexOf(z) < 0) { zones.push(z); zoneLabels.push(code + '→' + z); } }
      else { missCodes.push(code); }
    });

    if (!zones.length) {
      card.classList.add('hidden-by-search');
      if (resultEl) resultEl.textContent = '未找到该邮编对应分区';
      var q1 = $('wh-quote-' + key);
      if (q1) q1.innerHTML = '';
      return false;
    }

    card.classList.remove('hidden-by-search');
    var matchers = zones.map(makeZoneMatcher);
    card.querySelectorAll('.wh-table tbody tr').forEach(function (tr) {
      if (tr.classList.contains('wh-group-row')) return;
      var cells = tr.querySelectorAll('td');
      var matched = false;
      for (var i = 0; i < cells.length && !matched; i++) {
        var txt = cells[i].textContent.trim();
        for (var j = 0; j < matchers.length; j++) { if (matchers[j](txt)) { matched = true; break; } }
      }
      if (matched) tr.classList.add('wh-hit');
    });
    if (resultEl) resultEl.textContent = '分区：' + zoneLabels.join('，') + (missCodes.length ? '（' + missCodes.join('/') + ' 未命中）' : '');
    // 命中后渲染运费总价表（悉尼仓/墨尔本仓各一行）
    renderWhQuote(key, codes);
    return true;
  }

  // ---- 运费总价：max(首重 + Per/1KG × 进位计费重, Minimum) ×(1+燃油%)×(1+GST%)，单位 AUD ----
  function whColIndex(headers, keywords) {
    for (var i = 0; i < headers.length; i++) {
      var h = String(headers[i] == null ? '' : headers[i]).replace(/\s+/g, '').toLowerCase();
      for (var k = 0; k < keywords.length; k++) {
        if (h.indexOf(keywords[k]) >= 0) return i;
      }
    }
    return -1;
  }
  function whNum(v) { var n = parseFloat(v); return isNaN(n) ? null : n; }
  function fmt2(n) { return (Math.round(n * 100) / 100).toString(); }

  // 仓别名（从 price_table 标题提取 悉尼仓/墨尔本仓）
  function warehouseLabel(title) {
    var t = String(title || '');
    if (t.indexOf('悉尼') >= 0) return '悉尼仓';
    if (t.indexOf('墨尔本') >= 0) return '墨尔本仓';
    var m = t.match(/[（(]([^）)]+)[）)]/);
    return m ? m[1] : '';
  }

  // 计算某表某邮编在各价格表（各仓）的报价行；weight 为 0/空 时只出公式不出总价
  function computeWarehouseRows(key, code, weight) {
    var data = whData[key];
    if (!data) return [];
    var zone = (whMaps[key] || {})[code];
    if (!zone) return [];
    var matcher = makeZoneMatcher(zone);
    var fuel = Number(whSettings.fuel_rate) || 0;
    var gst = Number(whSettings.gst_rate) || 0;
    var fuelMult = 1 + fuel / 100;
    var gstMult = 1 + gst / 100;
    var w = weight > 0 ? Math.ceil(weight) : 0;   // 向上取整 KG

    var out = [];
    (data.sections || []).forEach(function (sec) {
      if (sec.type !== 'price_table') return;
      var headers = sec.headers || [];
      var baseIdx = whColIndex(headers, ['首重']);
      var perIdx = whColIndex(headers, ['per', '单价', '/1kg']);
      var minIdx = whColIndex(headers, ['minimum', '最低', 'min']);
      var zoneIdx = whColIndex(headers, ['zone', '分区']);
      // 附加费等表没有 首重/Per 列 → 跳过
      if (baseIdx < 0 || perIdx < 0) return;
      if (zoneIdx < 0) zoneIdx = (headers.length > 3) ? 1 : 0;

      var hitRow = null;
      (sec.rows || []).forEach(function (row) {
        if (hitRow) return;
        var cell = String(row[zoneIdx] == null ? '' : row[zoneIdx]).trim();
        if (matcher(cell)) hitRow = row;
      });
      if (!hitRow) return;

      var base = whNum(hitRow[baseIdx]);
      var per = whNum(hitRow[perIdx]);
      var minv = minIdx >= 0 ? whNum(hitRow[minIdx]) : null;
      if (base == null || per == null) return;

      var row = {
        warehouse: warehouseLabel(sec.title), code: code, zone: zone,
        base: base, per: per, min: minv, w: w
      };
      if (w > 0) {
        var sub = base + per * w;
        if (minv != null && minv > sub) sub = minv;
        row.total = sub * fuelMult * gstMult;
        row.formula = 'max(' + fmt2(base) + '+' + fmt2(per) + '×' + w + (minv != null ? ', ' + fmt2(minv) : '') +
          ')×' + fmt2(fuelMult) + '×' + fmt2(gstMult);
      } else {
        row.total = null;
        row.formula = 'max(首重+Per×计费重' + (minv != null ? ', Min' : '') + ')×(1+燃油' + fuel + '%)×(1+GST' + gst + '%)';
      }
      out.push(row);
    });
    return out;
  }

  function currentWeightForWh() {
    // 顶部单查用 hero 计费重量；左侧批量用侧栏计费重量。取有值的一个。
    var hero = parseFloat(($('cbWeight') || {}).value);
    var side = parseFloat(($('batchWeight') || {}).value);
    if (!isNaN(hero) && hero > 0) return hero;
    if (!isNaN(side) && side > 0) return side;
    return 0;
  }

  function renderWhQuote(key, codes) {
    var host = $('wh-quote-' + key);
    if (!host) {
      var card = $('wh-card-' + key);
      if (!card) return;
      host = document.createElement('div');
      host.id = 'wh-quote-' + key;
      host.className = 'wh-quote';
      card.appendChild(host);
    }
    var weight = currentWeightForWh();
    var allRows = [];
    (codes || []).forEach(function (code) {
      allRows = allRows.concat(computeWarehouseRows(key, code, weight));
    });
    if (!allRows.length) { host.innerHTML = ''; return; }

    var h = '<div class="wh-quote-title">最终全程运费总价（AUD）</div>';
    h += '<table class="wh-quote-table"><thead><tr>' +
      '<th>仓别</th><th>邮编</th><th>分区</th><th>计费重(kg)</th><th>计算公式</th><th>总价(AUD)</th>' +
      '</tr></thead><tbody>';
    allRows.forEach(function (r) {
      h += '<tr>' +
        '<td>' + esc(r.warehouse) + '</td>' +
        '<td>' + esc(r.code) + '</td>' +
        '<td>' + esc(r.zone) + '</td>' +
        '<td>' + (r.w > 0 ? r.w : '—') + '</td>' +
        '<td class="wh-quote-formula">' + esc(r.formula) + '</td>' +
        '<td class="wh-quote-total">' + (r.total != null ? fmt2(r.total) : '<span class="wh-quote-need">填重量后计算</span>') + '</td>' +
        '</tr>';
    });
    h += '</tbody></table>';
    h += '<div class="wh-quote-note">备注：总价 = max(首重 + Per/1KG × 计费重, Minimum) ×(1+燃油率)×(1+10%GST)，重量不足 1KG 按 1KG 进位。</div>';
    host.innerHTML = h;
  }

  // 对全部 4 张海外仓卡执行搜索
  function searchWarehouseInline(codes) {
    WH_KEYS.forEach(function (key) { searchWarehouseInlineOne(key, codes); });
  }
  function clearWarehouseSearch() {
    WH_KEYS.forEach(function (key) { searchWarehouseInlineOne(key, []); });
  }

  // ============================================================
  // 渠道搜索（复用 article.js 全局函数）
  // ============================================================

  function clearChannelSearch() {
    document.querySelectorAll('.channel-table tbody tr').forEach(function (tr) {
      tr.classList.remove('highlight');
      tr.classList.remove('hidden-by-search');
    });
    document.querySelectorAll('.channel-table tbody td.merged-cell-highlight').forEach(function (td) {
      td.classList.remove('merged-cell-highlight');
    });
    document.querySelectorAll('.module-channel').forEach(function (m) {
      m.classList.remove('hidden-by-search');
      m.classList.remove('search-mode');
    });
    if (typeof unmergeAllChannelTables === 'function') unmergeAllChannelTables();
    if (typeof mergeAllChannelTables === 'function') mergeAllChannelTables();
    if (typeof window._syncChannelNavWithSearch === 'function') window._syncChannelNavWithSearch();
  }

  // 多邮编并集高亮/筛选渠道行
  function renderMatchedRowsForCodes(codes) {
    if (typeof unmergeAllChannelTables === 'function') unmergeAllChannelTables();
    document.querySelectorAll('.module-channel').forEach(function (module) {
      var wrapper = module.querySelector('.channel-table-wrapper');
      if (!wrapper) { module.classList.add('hidden-by-search'); return; }
      var matchedRows = [];
      wrapper.querySelectorAll('tbody tr').forEach(function (tr) {
        var range = tr.dataset.postcodeRange;
        var hit = range && codes.some(function (code) {
          return typeof isPostcodeInRange === 'function' && isPostcodeInRange(code, range);
        });
        if (hit) { matchedRows.push(tr); }
        else { tr.classList.add('hidden-by-search'); }
      });
      if (matchedRows.length === 0) {
        module.classList.add('hidden-by-search');
      } else {
        module.classList.add('search-mode');
        if (typeof highlightMatchedRowsWithMergedCells === 'function') {
          highlightMatchedRowsWithMergedCells(matchedRows);
        }
      }
    });
    if (typeof window._syncChannelNavWithSearch === 'function') window._syncChannelNavWithSearch();
    if (typeof mergeAllChannelTables === 'function') mergeAllChannelTables();
    document.querySelectorAll('.channel-table-wrapper').forEach(function (w) { w.style.width = '100%'; });
  }

  // ============================================================
  // 统一搜索入口
  // ============================================================

  // 顶部单查：走 article.js 的 globalSearchPostcode（渠道 + 派送/距离），我们额外并行查海外仓。
  function afterTopSearch() {
    var code = oneCode(($('globalPostcodeInput') || {}).value);
    if (code) searchWarehouseInline([code]);
    else clearWarehouseSearch();
  }

  function renderBatchSummary(codes) {
    var box = $('batchSummary');
    if (!box) return;
    box.innerHTML = '';
    codes.forEach(function (code) {
      // 渠道命中？
      var chHit = false;
      document.querySelectorAll('.module-channel .channel-table-wrapper tbody tr').forEach(function (tr) {
        if (chHit) return;
        var range = tr.dataset.postcodeRange;
        if (range && typeof isPostcodeInRange === 'function' && isPostcodeInRange(code, range)) chHit = true;
      });
      // 海外仓命中？（任一表有分区）
      var whHit = WH_KEYS.some(function (k) { return whMaps[k] && whMaps[k][code]; });
      var matched = chHit || whHit;
      var item = document.createElement('div');
      item.className = 'csb-pc-item' + (matched ? '' : ' miss');
      item.innerHTML = '<span class="csb-pc-code">' + esc(code) + '</span>' +
        '<span class="csb-pc-badge">' + (matched ? '可派送' : '未命中') + '</span>';
      box.appendChild(item);
    });
  }

  function runBatch() {
    var codes = parseCodes($('batchPcInput') ? $('batchPcInput').value : '');
    var summary = $('batchSummary');
    if (!codes.length) {
      clearChannelSearch();
      clearWarehouseSearch();
      if (summary) summary.innerHTML = '<div class="csb-pc-hint">请输入至少一个 4 位邮编</div>';
      return;
    }
    // 与顶部单查互斥：清空顶部输入与其结果
    var topInput = $('globalPostcodeInput');
    if (topInput) topInput.value = '';
    var topResult = $('globalSearchResult');
    if (topResult) { topResult.textContent = ''; topResult.className = 'csb-hero-result'; }

    renderMatchedRowsForCodes(codes);
    searchWarehouseInline(codes);
    renderBatchSummary(codes);
  }

  function clearBatch() {
    if ($('batchPcInput')) $('batchPcInput').value = '';
    if ($('batchWeight')) $('batchWeight').value = '';
    if ($('batchSummary')) $('batchSummary').innerHTML = '';
    clearChannelSearch();
    clearWarehouseSearch();
  }

  // ============================================================
  // 初始化
  // ============================================================

  function init() {
    // 页面加载即拉 4 张海外仓表 + 运费参数（燃油率）
    loadWarehouseTables();
    var inlineEl = $('whInline');
    var whDir = (inlineEl && inlineEl.getAttribute('data-wh-dir')) || 'warehouse_au_dahuo';
    fetch('/api/warehouse-settings?dir=' + encodeURIComponent(whDir))
      .then(function (r) { return r.json(); })
      .then(function (res) { if (res && res.success && res.data) whSettings = res.data; })
      .catch(function () {});

    // 计费重量输入框变化 → 对当前已命中的海外仓卡重算总价
    function recomputeQuotes() {
      var top = oneCode(($('globalPostcodeInput') || {}).value);
      var codes = top ? [top] : parseCodes(($('batchPcInput') || {}).value);
      if (codes.length) WH_KEYS.forEach(function (k) { if (!$('wh-card-' + k).classList.contains('hidden-by-search')) renderWhQuote(k, codes); });
    }
    ['cbWeight', 'batchWeight'].forEach(function (id) {
      var el = $(id);
      if (el) el.addEventListener('input', recomputeQuotes);
    });

    // 顶部单查：包裹 article.js 的 globalSearchPostcode，跑完渠道后并行查海外仓。
    var origSearch = window.globalSearchPostcode;
    if (typeof origSearch === 'function') {
      window.globalSearchPostcode = function () {
        var r = origSearch.apply(this, arguments);
        // 顶部批量互斥：有顶部值时清空左侧批量框
        if (oneCode(($('globalPostcodeInput') || {}).value)) {
          if ($('batchPcInput')) $('batchPcInput').value = '';
          if ($('batchSummary')) $('batchSummary').innerHTML = '';
        }
        afterTopSearch();
        return r;
      };
    }

    // 左侧批量查询
    var queryBtn = $('batchQueryBtn');
    var clearBtn = $('batchClearBtn');
    var batchInput = $('batchPcInput');
    if (queryBtn) queryBtn.addEventListener('click', runBatch);
    if (clearBtn) clearBtn.addEventListener('click', clearBatch);
    if (batchInput) {
      batchInput.addEventListener('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runBatch(); }
      });
    }

    // 顶部 hero「查询报价」按钮：触发一次统一搜索
    var heroBtn = $('csbHeroQuery');
    if (heroBtn) {
      heroBtn.addEventListener('click', function () {
        if (typeof window.globalSearchPostcode === 'function') window.globalSearchPostcode();
        else afterTopSearch();
      });
    }

    // 顶部「清空」按钮：清空所有查询态
    var clearAll = $('csbClearAll');
    if (clearAll) {
      clearAll.addEventListener('click', function () {
        var ti = $('globalPostcodeInput');
        if (ti) ti.value = '';
        if (typeof window.globalSearchPostcode === 'function') window.globalSearchPostcode();
        clearBatch();
      });
    }

    // 海外仓目录项：滚动定位到对应卡片（不再切视图）
    document.querySelectorAll('.csb-wh-nav-item[data-scroll-target]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var target = document.querySelector(btn.getAttribute('data-scroll-target'));
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        document.querySelectorAll('.csb-wh-nav-item.active').forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
