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
  // 需求：四个快递（海外仓）查询结果不展示「最终全程运费总价」表，仅保留价格表命中行高亮。
  // 如需恢复运费总价表渲染，把此开关改回 true 即可。
  var WH_SHOW_QUOTE = false;
  var whMaps = {};   // { key: postcode_zone_map }
  var whData = {};   // { key: 完整 sheet data（含 sections，用于运费总价计算） }
  var whReady = {};  // { key: true } 渲染完成
  var whSettings = { monthly: {}, fuel_rates: {}, exchange_rates: {}, gst_rate: 10 };  // 海外仓运费参数（各表分月：燃油率/汇率；GST 固定 10%）

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

  // ---- 偏远附加费（仅 border / toll 家族，含其 _copy 副本表）----
  // 承运商家族：border_copy2 → border、toll_copy → toll，其余原样。
  function whFamily(key) { return String(key || '').replace(/_copy.*$/, ''); }
  function isRemoteFamily(key) { var f = whFamily(key); return f === 'border' || f === 'toll'; }
  function fmtFee(n) { var v = Math.round(Number(n) * 100) / 100; return isNaN(v) ? esc(n) : v; }

  var _remoteFeeCache = {};   // codesKey -> Promise({ code: {border, toll} })
  function fetchRemoteSurcharge(codes) {
    var ck = codes.join(',');
    if (_remoteFeeCache[ck]) return _remoteFeeCache[ck];
    var p = fetch('/api/remote-surcharge?codes=' + encodeURIComponent(ck))
      .then(function (r) { return r.json(); })
      .then(function (res) { return (res && res.success && res.data) ? res.data : {}; })
      .catch(function () { return {}; });
    _remoteFeeCache[ck] = p;
    return p;
  }

  function suburbsHtml(item) {
    if (!item || !item.suburbs || !item.suburbs.length) return '';
    var names = item.suburbs.map(esc).join('、');
    var more = (item.count > item.suburbs.length) ? (' 等' + item.count + '个地区') : '';
    return '<span class="wh-remote-sub">（' + names + more + '）</span>';
  }

  // 把某张 border/toll 卡的偏远附加费渲染到 #wh-remote-<key>；无数据显示「无偏远附加费」
  function renderRemoteFee(key, codes, data) {
    var host = $('wh-remote-' + key);
    if (!host) return;
    var fam = whFamily(key);
    if (!isRemoteFamily(key) || !codes || !codes.length) { host.innerHTML = ''; return; }
    var h = '';
    codes.forEach(function (code) {
      var rec = (data && data[code]) ? data[code][fam] : null;
      var lines = '';
      if (fam === 'border' && rec && rec.has) {
        lines = rec.tiers.map(function (t) {
          return '<div class="wh-remote-line"><b>' + esc(t.ras_tier) + '</b>：纸箱 ' +
            fmtFee(t.parcel_fee) + ' RMB/票，木箱木架托盘 ' + fmtFee(t.bulk_fee) + ' RMB/票</div>';
        }).join('');
      } else if (fam === 'toll' && rec && rec.has) {
        lines = rec.groups.map(function (g) {
          return '<div class="wh-remote-line">偏远费 ' + fmtFee(g.fee) + ' RMB/票</div>';
        }).join('');
      }
      if (!lines) lines = '<div class="wh-remote-line wh-remote-none">无偏远附加费</div>';
      h += '<div class="wh-remote-block"><div class="wh-remote-title">偏远地区附加费（' +
        esc(code) + '）</div>' + lines + '</div>';
    });
    host.innerHTML = h;
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

  // 「选用公式」的重量档常量与算价（与后台编辑器 wseCalcCell 一致）
  var WH_FORMULA_W = { '15': 15, '30': 30, '70': 70, '100': 100, '500': 500 };
  // 尾程列定位：表头含「尾程操作费」→op，含「尾程运费」→per（原始列序，隐藏与否不影响读取）
  function whTailCols(headers) {
    var res = { per: -1, op: -1 };
    (headers || []).forEach(function (hd, ci) {
      var k = String(hd == null ? '' : hd).replace(/\s+/g, '');
      if (res.op < 0 && k.indexOf('尾程操作费') >= 0) res.op = ci;
      else if (res.per < 0 && k.indexOf('尾程运费') >= 0) res.per = ci;
    });
    return res;
  }
  function whRowNum(row, ci) {
    if (ci < 0 || !row) return 0;
    var n = parseFloat(row[ci]);
    return isNaN(n) ? 0 : n;
  }
  // Wkg 单价 = 【头程费用×W + (尾程运费×W + 尾程操作费) × 汇率 × (1+燃油率/100)】 / W
  function whCalcFormula(w, head, rate, fuel, per, op) {
    if (!(w > 0) || !(rate > 0)) return null;
    var tail = (per * w + op) * rate * (1 + fuel / 100);
    return (head * w + tail) / w;
  }

  // 渲染单个 price_table section 为 HTML（含合并单元格无关，海外仓表简单，逐行输出）
  // key 用于取月度全局参数（头程/汇率/燃油）以计算「选用公式」的重量档单价列。
  function renderWhTable(sec, key) {
    var rows = sec.rows || [];
    var headers = sec.headers || [];
    if (!rows.length && !headers.length) return '';
    // 隐藏「Minimum Charge / 最低收费」列，以及后台标记为「前台隐藏」（col_hidden）的列——仅前台展示隐藏，原始数据不动
    var colHidden = sec.col_hidden || [];
    var keep = [];
    for (var ci = 0; ci < headers.length; ci++) {
      var hk = String(headers[ci] == null ? '' : headers[ci]).replace(/\s+/g, '').toLowerCase();
      if (hk.indexOf('minimum') >= 0 || hk.indexOf('最低') >= 0) continue;
      // 前台价格表不展示「尾程操作费AUD / 尾程运费Per /1 KG」两列（仅供公式列取值算价，算价按原始行读取不受影响）
      if (hk.indexOf('尾程操作费') >= 0 || hk.indexOf('尾程运费') >= 0) continue;
      if (colHidden[ci]) continue;
      keep.push(ci);
    }
    if (!keep.length) { for (var ck = 0; ck < headers.length; ck++) keep.push(ck); }
    var pick = function (row) { return keep.map(function (i) { return row[i]; }); };
    // 「选用公式」的重量档列（col_formulas: '' | '15'|'30'|'70'|'100'|'500'），按各行自有尾程列算价
    var fmap = {};
    (sec.col_formulas || []).forEach(function (fk, ci) {
      var w = WH_FORMULA_W[String(fk)];
      if (w) fmap[ci] = w;
    });
    var hasFormula = Object.keys(fmap).length > 0;
    var fTail = hasFormula ? whTailCols(headers) : null;
    var fHead = hasFormula ? whMonthParam(key, 'unit_price', 0) : 0;
    var fRate = hasFormula ? whMonthParam(key, 'exchange_rate', 0) : 0;
    var fFuel = hasFormula ? whMonthParam(key, 'fuel_rate', 0) : 0;
    // 前台展示排序：整行按「15kg」列从低到高排（其余列随该行一起移动）。仅前台显示排序，后台数据与编辑不变。
    // 15kg 通常是公式列（col_formulas==='15'），按算出的单价排；否则按该列数字排。
    // 含州名分组行的表不排（避免打乱分组）；空/无法算价的行沉底并保持原相对次序（稳定排序）。
    var col15 = -1, col15IsFormula = false;
    (sec.col_formulas || []).forEach(function (fk, ci) { if (col15 < 0 && String(fk) === '15') { col15 = ci; col15IsFormula = true; } });
    if (col15 < 0) {
      for (var h15 = 0; h15 < headers.length; h15++) {
        if (String(headers[h15] == null ? '' : headers[h15]).replace(/\s+/g, '').toLowerCase() === '15kg') { col15 = h15; break; }
      }
    }
    var hasGroupRow = rows.some(function (row) {
      var filled = row.filter(function (c) { return c !== '' && c !== null && c !== undefined; });
      return row.length >= 3 && filled.length === 1 && (row[0] !== '' && row[0] != null);
    });
    var displayRows = rows;
    if (col15 >= 0 && !hasGroupRow) {
      var key15 = function (row) {
        var v;
        if (col15IsFormula) v = whCalcFormula(15, fHead, fRate, fFuel, whRowNum(row, fTail.per), whRowNum(row, fTail.op));
        else { v = parseFloat(row[col15]); if (isNaN(v)) v = null; }
        return (v == null) ? Infinity : v;
      };
      displayRows = rows.map(function (row, i) { return { row: row, i: i, k: key15(row) }; })
        .sort(function (a, b) { return (a.k - b.k) || (a.i - b.i); })
        .map(function (o) { return o.row; });
    }
    var h = '<div class="wh-table-wrap"><table class="wh-table"><thead><tr>';
    pick(headers).forEach(function (th) { h += '<th>' + esc(th) + '</th>'; });
    h += '</tr></thead><tbody>';
    displayRows.forEach(function (row) {
      // 州名分组行（只有首格有值，其余空）：整行合并展示（按原始行判定）
      var filled = row.filter(function (c) { return c !== '' && c !== null && c !== undefined; });
      if (row.length >= 3 && filled.length === 1 && (row[0] !== '' && row[0] != null)) {
        h += '<tr class="wh-group-row"><td colspan="' + keep.length + '">' + esc(row[0]) + '</td></tr>';
        return;
      }
      h += '<tr>';
      if (hasFormula) {
        var per = whRowNum(row, fTail.per);
        var op = whRowNum(row, fTail.op);
        keep.forEach(function (ci) {
          if (fmap[ci] != null) {
            var v = whCalcFormula(fmap[ci], fHead, fRate, fFuel, per, op);
            h += '<td>' + (v == null ? '' : v.toFixed(2)) + '</td>';
          } else {
            h += '<td>' + esc(row[ci]) + '</td>';
          }
        });
      } else {
        pick(row).forEach(function (c) { h += '<td>' + esc(c) + '</td>'; });
      }
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
    // 文章内容备注（后台「月度参数面板下方、价格表上方」的富文本）
    if (data && data.panel_note_html) {
      html += '<div class="wh-panel-note">' + whRichtextToHtml(data.panel_note_html) + '</div>';
    }
    html += '<div class="wh-card-result" id="wh-result-' + key + '"></div>';
    // 「价格说明」上方的挂载容器：border/toll→偏远附加费；仅基础 tfm 卡→地址详情。
    // （放在第一个富文本段之前；找不到富文本则末尾兜底）
    var preRichtextHtml = '';
    if (isRemoteFamily(key)) preRichtextHtml = '<div class="wh-remote-fee" id="wh-remote-' + key + '"></div>';
    else if (whFamily(key) === 'tfm') preRichtextHtml = '<div class="tfm-addr-detail" id="tfmAddressDetail-' + key + '"></div>';
    var preInserted = false;
    (data.sections || []).forEach(function (sec) {
      if (sec && sec.hidden) return;   // 后台设为「前台隐藏」的块：文章前台不展示
      if (preRichtextHtml && !preInserted && sec.type === 'richtext') {
        html += preRichtextHtml; preInserted = true;
      }
      if (sec.type === 'price_table') {
        if (sec.title) html += '<div class="wh-card-section-title">' + esc(sec.title) + '</div>';
        html += renderWhTable(sec, key);
      } else if (sec.type === 'richtext') {
        if (sec.title) html += '<div class="wh-card-section-title">' + esc(sec.title) + '</div>';
        html += '<div class="wh-richtext">' + whRichtextToHtml(sec.html || '') + '</div>';
      }
    });
    if (preRichtextHtml && !preInserted) html += preRichtextHtml;
    card.innerHTML = html;
    // 同步侧栏目录名称：后台重命名后目录与卡片标题保持一致
    var navItem = document.querySelector('.csb-wh-nav-item[data-scroll-target="#wh-card-' + key + '"]');
    if (navItem) navItem.textContent = name;
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

  // 同步「渠道目录」里该快递报价表的目录项显示：卡片被搜索隐藏时，目录项也隐藏，
  // 避免出现「目录里一大堆、点了却定位到隐藏卡片没反应」。
  function setWhNavHidden(key, hidden) {
    var navItem = document.querySelector('.csb-wh-nav-item[data-scroll-target="#wh-card-' + key + '"]');
    if (!navItem) return;
    var li = navItem.closest('li') || navItem;
    li.classList.toggle('hidden-in-nav', hidden);
    navItem.classList.toggle('hidden-in-nav', hidden);
  }

  // 对单张海外仓卡按邮编集合查询/高亮；返回是否命中
  function searchWarehouseInlineOne(key, codes) {
    var card = $('wh-card-' + key);
    if (!card || !whMaps[key]) return false;
    var map = whMaps[key];
    // 清高亮与行隐藏
    card.querySelectorAll('.wh-table tbody tr.wh-hit').forEach(function (tr) { tr.classList.remove('wh-hit'); });
    card.querySelectorAll('.wh-table tbody tr.wh-row-hidden').forEach(function (tr) { tr.classList.remove('wh-row-hidden'); });
    var resultEl = $('wh-result-' + key);

    if (!codes || !codes.length) {
      card.classList.remove('hidden-by-search');
      setWhNavHidden(key, false);   // 清空搜索：目录项恢复显示
      if (resultEl) resultEl.textContent = '';
      var q0 = $('wh-quote-' + key);
      if (q0) q0.innerHTML = '';
      var rf0 = $('wh-remote-' + key);
      if (rf0) { rf0.innerHTML = ''; rf0.removeAttribute('data-codes'); }
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
      // TFM 家族卡若已挂着地址详情，则即使该邮编无 TFM 分区也保持卡片可见（否则地址详情会被一起隐藏）
      var tfmHost = (whFamily(key) === 'tfm') ? $('tfmAddressDetail-' + key) : null;
      var tfmAddr = tfmHost && tfmHost.innerHTML.trim();
      card.classList.toggle('hidden-by-search', !tfmAddr);
      setWhNavHidden(key, !tfmAddr);
      if (resultEl) resultEl.textContent = tfmAddr ? '该邮编无 TFM 分区（仅显示地址详情）' : '未找到该邮编对应分区';
      var q1 = $('wh-quote-' + key);
      if (q1) q1.innerHTML = '';
      var rf1 = $('wh-remote-' + key);
      if (rf1) { rf1.innerHTML = ''; rf1.removeAttribute('data-codes'); }
      return false;
    }

    card.classList.remove('hidden-by-search');
    setWhNavHidden(key, false);   // 命中：确保目录项显示
    var matchers = zones.map(makeZoneMatcher);
    // 查询后只展示命中行，其余行隐藏（避免整表几百行太长）
    card.querySelectorAll('.wh-table tbody tr').forEach(function (tr) {
      if (tr.classList.contains('wh-group-row')) { tr.classList.add('wh-row-hidden'); return; }
      var cells = tr.querySelectorAll('td');
      var matched = false;
      for (var i = 0; i < cells.length && !matched; i++) {
        var txt = cells[i].textContent.trim();
        for (var j = 0; j < matchers.length; j++) { if (matchers[j](txt)) { matched = true; break; } }
      }
      if (matched) tr.classList.add('wh-hit');
      else tr.classList.add('wh-row-hidden');
    });
    if (resultEl) resultEl.textContent = '分区：' + zoneLabels.join('，') + (missCodes.length ? '（' + missCodes.join('/') + ' 未命中）' : '');
    // 命中后渲染运费总价表（悉尼仓/墨尔本仓各一行）
    renderWhQuote(key, codes);
    // border / toll（含副本）：查询并展示偏远附加费（异步，带竞态兜底）
    if (isRemoteFamily(key)) {
      var rfHost = $('wh-remote-' + key);
      var ck = codes.join(',');
      if (rfHost) rfHost.setAttribute('data-codes', ck);
      fetchRemoteSurcharge(codes).then(function (data) {
        var h2 = $('wh-remote-' + key);
        if (!h2 || h2.getAttribute('data-codes') !== ck) return;   // 已被更晚的搜索取代
        renderRemoteFee(key, codes, data);
      });
    }
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

  // 取某表「当前月」的参数（燃油率/汇率/头程单价）；优先客户端当前月的 monthly，缺失回退服务器解析值
  function whMonthParam(key, field, def) {
    var monthly = whSettings.monthly || {};
    var rec = (monthly[key] || {})[String(new Date().getMonth() + 1)] || {};
    var v = parseFloat(rec[field]);
    if (!isNaN(v)) return v;
    var fallbackMap = (field === 'fuel_rate') ? whSettings.fuel_rates
      : (field === 'exchange_rate') ? whSettings.exchange_rates : null;
    var fv = fallbackMap ? parseFloat(fallbackMap[key]) : NaN;
    return isNaN(fv) ? def : fv;
  }

  // 仓别名（从 price_table 标题提取 悉尼仓/墨尔本仓）
  function warehouseLabel(title) {
    var t = String(title || '');
    if (t.indexOf('悉尼') >= 0) return '悉尼仓';
    if (t.indexOf('墨尔本') >= 0) return '墨尔本仓';
    var m = t.match(/[（(]([^）)]+)[）)]/);
    return m ? m[1] : '';
  }

  // 头程三种运输方式（只有头程单价不同，尾程/邮编单价相同）
  var WH_MODES = [
    { name: '大陆空运', field: 'unit_price' },
    { name: '香港空运', field: 'hk_unit_price' },
    { name: '海运', field: 'sea_unit_price' }
  ];

  // 计算某表某邮编在各价格表（各仓）的报价行；weight 为 0/空 时公式照出、数字列留空
  function computeWarehouseRows(key, code, weight) {
    var data = whData[key];
    if (!data) return [];
    var zone = (whMaps[key] || {})[code];
    if (!zone) return [];
    var matcher = makeZoneMatcher(zone);
    // 三种头程单价：大陆空运 / 香港空运 / 海运
    var units = {
      '大陆空运': whMonthParam(key, 'unit_price', 0),
      '香港空运': whMonthParam(key, 'hk_unit_price', 0),
      '海运': whMonthParam(key, 'sea_unit_price', 0)
    };
    var fuel = whMonthParam(key, 'fuel_rate', 0);
    var rate = whMonthParam(key, 'exchange_rate', 0);   // 澳币→人民币汇率（0=不折算）
    var fuelMult = 1 + fuel / 100;
    var fuelPctTxt = (Math.round((100 + fuel) * 100) / 100) + '%';   // 如 13% → "113%"
    var w = weight > 0 ? Math.ceil(weight) : 0;   // 除4000计费重（进位 KG）

    var out = [];
    (data.sections || []).forEach(function (sec) {
      if (sec.type !== 'price_table') return;
      var headers = sec.headers || [];
      var perIdx = whColIndex(headers, ['per', '单价', '/1kg']);
      var zoneIdx = whColIndex(headers, ['zone', '分区']);
      // 附加费等表没有 Per/1KG 列 → 跳过
      if (perIdx < 0) return;
      if (zoneIdx < 0) zoneIdx = (headers.length > 3) ? 1 : 0;

      var hitRow = null;
      (sec.rows || []).forEach(function (row) {
        if (hitRow) return;
        var cell = String(row[zoneIdx] == null ? '' : row[zoneIdx]).trim();
        if (matcher(cell)) hitRow = row;
      });
      if (!hitRow) return;

      var per = whNum(hitRow[perIdx]);   // 符合邮编对应的单价（Per/1KG，AUD/kg）
      if (per == null) return;

      // 尾程（三种方式相同）：公式列始终把「除4000计费重」按文字显示（不代入数字）
      var tailF = fmt2(per) + '*' + fuelPctTxt + '*' + fmt2(rate) + '*除4000计费重';
      var hasNum = (w > 0 && rate > 0);
      var tail = hasNum ? (per * fuelMult * rate * w) : null;   // 尾程（元）= 邮编单价×(1+燃油)×汇率×计费重

      var row = {
        warehouse: warehouseLabel(sec.title), code: code, zone: zone,
        per: per, w: w, rate: rate, fuelMult: fuelMult, fuelPctTxt: fuelPctTxt,
        units: units, label: w > 0 ? (w + 'KG') : '—', tailF: tailF, tail: tail, hasNum: hasNum
      };
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

  function fmtM(n) { return (Math.round(n * 100) / 100).toFixed(2); }

  function renderWhQuote(key, codes) {
    var host = $('wh-quote-' + key);
    // 四个快递（海外仓）查询结果不展示「最终全程运费总价」表：清空已有内容并跳过渲染。
    if (!WH_SHOW_QUOTE) { if (host) host.innerHTML = ''; return; }
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

    // 按仓分组，保持出现顺序（悉尼仓/墨尔本仓）
    var order = [];
    var groups = {};
    allRows.forEach(function (r) {
      var wh = r.warehouse || '';
      if (!groups[wh]) { groups[wh] = []; order.push(wh); }
      groups[wh].push(r);
    });

    var h = '';
    order.forEach(function (wh) {
      var rows = groups[wh];
      // 每仓出三张表：大陆空运 / 香港空运 / 海运（仅头程单价不同，尾程相同）
      WH_MODES.forEach(function (mode) {
        var whTxt = wh ? '（' + wh + '）' : '';
        var title = '最终全程运费总价 — ' + mode.name + whTxt;
        h += '<div class="wh-quote-title">' + esc(title) + '</div>';
        h += '<table class="wh-quote-table"><thead><tr>' +
          '<th>邮编</th><th>公斤段</th><th>头程计算公式</th><th>尾程计算公式</th>' +
          '<th>除4000单价</th><th>除6000单价</th><th>总价(元)</th>' +
          '</tr></thead><tbody>';
        var sum = 0, hasTotal = false;
        rows.forEach(function (r) {
          var noRate = (r.w > 0 && !(r.rate > 0));   // 有重量但没设汇率
          var unit = (r.units && r.units[mode.name] != null) ? r.units[mode.name] : 0;
          var headF = fmt2(unit) + '*' + r.fuelPctTxt + '*' + fmt2(r.rate) + '*除4000计费重';
          var total = null, div4000 = null, div6000 = null;
          if (r.hasNum) {
            var head = unit * r.fuelMult * r.rate * r.w;   // 头程（元）= 头程单价×(1+燃油)×汇率×计费重
            total = head + r.tail;                          // 总价 = 头程 + 尾程
            div4000 = total / r.w;                          // 除4000单价 = 总价 ÷ 除4000计费重
            div6000 = total / (r.w * 4000 / 6000);          // 除6000单价 = 总价 ÷ 除6000计费重
            sum += total; hasTotal = true;
          }
          var cell = function (v) {
            if (total != null && v != null) return fmtM(v);
            return '<span class="wh-quote-need">' + (noRate ? '未设汇率' : '填重量后计算') + '</span>';
          };
          h += '<tr>' +
            '<td>' + esc(r.code) + '</td>' +
            '<td>' + esc(r.label) + '</td>' +
            '<td class="wh-quote-formula">' + esc(headF) + '</td>' +
            '<td class="wh-quote-formula">' + esc(r.tailF) + '</td>' +
            '<td>' + cell(div4000) + '</td>' +
            '<td>' + cell(div6000) + '</td>' +
            '<td class="wh-quote-total">' + cell(total) + '</td>' +
            '</tr>';
        });
        h += '<tr class="wh-quote-sum-row"><td colspan="6">合计</td><td>' +
          (hasTotal ? fmtM(sum) : '<span class="wh-quote-need">—</span>') + '</td></tr>';
        h += '</tbody></table>';
      });
    });

    h += '<div class="wh-quote-note">备注：每仓分「大陆空运 / 香港空运 / 海运」三张表，三者仅头程单价不同、尾程相同。' +
      '头程 = 对应头程运输费用单价 ×(1+燃油率)× 汇率 × 除4000计费重；' +
      '尾程 = 邮编对应单价 ×(1+燃油率)× 汇率 × 除4000计费重；总价 = 头程 + 尾程；' +
      '除4000单价 = 总价 ÷ 除4000计费重，除6000单价 = 总价 ÷ 除6000计费重。除4000计费重 = 长×宽×高 ÷ 4000。</div>';
    // 后台自定义备注（免责声明），留空则用默认文案
    var data = whData[key] || {};
    var resultNote = (data.result_note != null && String(data.result_note).trim())
      ? String(data.result_note).trim()
      : '选择服务则代表已完整阅读渠道说明和费用详解。';
    h += '<div class="wh-quote-disclaimer">' + esc(resultNote) + '</div>';
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
  // explicitCode：地址流解析出的邮编（框里仍留地址文字时用它驱动海外仓卡搜索）。
  function afterTopSearch(explicitCode) {
    var code = (typeof explicitCode === 'string' && /^\d{4}$/.test(explicitCode))
      ? explicitCode : oneCode(($('globalPostcodeInput') || {}).value);
    if (code) {
      searchWarehouseInline([code]);
      // 有邮编就展开渠道目录：即使无卡派渠道命中，只要目录里还有可见项（含四张快递报价表）就展开，
      // 方便用户定位。article.js 的 _syncChannelNavWithSearch 只统计渠道卡，这里补上快递报价表卡。
      requestAnimationFrame(function () {
        var panel = $('channelNavPanel');
        if (!panel) return;
        var hasVisibleNav = !!document.querySelector('.channel-nav-list .channel-nav-link:not(.hidden-in-nav)');
        if (hasVisibleNav) panel.setAttribute('open', '');
      });
    } else {
      clearWarehouseSearch();
    }
  }

  // ============================================================
  // 地址查询（第三方 autocomplete/detail 代理）——仅本页顶部搜索框
  // ============================================================
  var _addrSession = '';   // autocomplete 返回的 session；detail 复用以「一次会话计费」
  var _addrReqToken = 0;   // autocomplete 竞态令牌

  function isPostcodeText(v) { return /^\d{4}$/.test(String(v || '').trim()); }
  function looksLikeAddress(v) { v = String(v || '').trim(); return v.length >= 2 && /[A-Za-z]/.test(v); }
  function addrSuggestEl() { return $('csbAddrSuggest'); }
  function closeAddrSuggest() { var el = addrSuggestEl(); if (el) { el.hidden = true; el.innerHTML = ''; } }
  function removeTfmAddrDetail() {
    document.querySelectorAll('.tfm-addr-detail').forEach(function (el) { el.innerHTML = ''; });
    setHeroPcBadge('');   // 地址详情清掉时，搜索框里的邮编徽标一并清除
  }
  // 搜索框内显示/清除「解析出的邮编」徽标（地址查询选中后展示）
  function setHeroPcBadge(pc) {
    var el = $('csbHeroPcBadge'); if (!el) return;
    if (pc) { el.textContent = '邮编 ' + pc; el.hidden = false; }
    else { el.textContent = ''; el.hidden = true; }
  }

  // 顶部输入变化（inline oninput）：空→复位；4 位邮编→沿用即时搜；地址→收起下拉、等回车/按钮
  window.csbHeroInput = function () {
    var v = ($('globalPostcodeInput') || {}).value || '';
    if (!v.trim()) {
      closeAddrSuggest(); removeTfmAddrDetail();
      if (typeof window.globalSearchPostcode === 'function') window.globalSearchPostcode();
      return;
    }
    if (isPostcodeText(v)) {
      closeAddrSuggest(); removeTfmAddrDetail();   // 切回邮编查询：上次地址详情一并清掉
      globalSearchPostcodeDebounced(); return;
    }
    closeAddrSuggest();
  };

  // 回车 / 点「查询报价」：邮编→原邮编搜索；地址→拉候选列表
  function csbHeroSubmit() {
    var v = (($('globalPostcodeInput') || {}).value || '').trim();
    if (!v) {
      closeAddrSuggest(); removeTfmAddrDetail();
      if (typeof window.globalSearchPostcode === 'function') window.globalSearchPostcode();
      return;
    }
    if (isPostcodeText(v)) {
      closeAddrSuggest(); removeTfmAddrDetail();
      if (typeof window.globalSearchPostcode === 'function') window.globalSearchPostcode();
      return;
    }
    if (looksLikeAddress(v)) { fetchAddrSuggest(v); return; }
    closeAddrSuggest();
  }

  function fetchAddrSuggest(q) {
    var el = addrSuggestEl(); if (!el) return;
    var token = ++_addrReqToken;
    _detailCache = {};   // 新一轮搜索：清掉上一轮 placeId→detail 缓存（session 已变）
    el.hidden = false;
    el.innerHTML = '<div class="csb-addr-hint">查询中…</div>';
    fetch('/api/address-autocomplete?q=' + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (token !== _addrReqToken) return;
        if (!res || !res.success || !res.data) { el.innerHTML = '<div class="csb-addr-hint">地址服务暂时不可用</div>'; return; }
        _addrSession = res.data.session || '';
        var sugg = res.data.suggestions || [];
        if (!sugg.length) { el.innerHTML = '<div class="csb-addr-hint">未找到匹配地址</div>'; return; }
        renderAddrSuggest(sugg);
      })
      .catch(function () { if (token === _addrReqToken) el.innerHTML = '<div class="csb-addr-hint">网络错误</div>'; });
  }

  var _detailCache = {};   // placeId -> Promise(detail json | null)，避免预取与点选重复调 detail

  function fetchDetailCached(placeId) {
    if (!placeId) return Promise.resolve(null);
    if (_detailCache[placeId]) return _detailCache[placeId];
    var url = '/api/address-detail/' + encodeURIComponent(placeId) + (_addrSession ? ('?session=' + encodeURIComponent(_addrSession)) : '');
    var p = fetch(url).then(function (r) { return r.json(); })
      .then(function (res) { return (res && res.success && res.data) ? res.data : null; })
      .catch(function () { return null; });
    _detailCache[placeId] = p;
    return p;
  }

  function postcodeOf(det) {
    var pc = det && det.place && det.place.addressParts && det.place.addressParts.postcode;
    pc = String(pc || '').replace(/[^0-9]/g, '');
    return /^\d{4}$/.test(pc) ? pc : '';
  }

  function renderAddrSuggest(sugg) {
    var el = addrSuggestEl(); if (!el) return;
    el.innerHTML = '';
    var token = _addrReqToken;   // 与本次 autocomplete 绑定，过期结果不回填
    sugg.forEach(function (s) {
      var item = document.createElement('button');
      item.type = 'button';
      item.className = 'csb-addr-item';
      item.innerHTML = '<span class="csb-addr-text">'
        + '<span class="csb-addr-primary">' + esc(s.primaryText || s.fullText || '') + '</span>'
        + '<span class="csb-addr-line2">'
        + (s.secondaryText ? '<span class="csb-addr-secondary">' + esc(s.secondaryText) + '</span>' : '')
        + '<span class="csb-addr-pc" data-pc-slot="1">…</span>'
        + '</span>'
        + '</span>';
      item.addEventListener('click', function () { pickAddress(s); });
      el.appendChild(item);
      // 预取邮编填入右侧框（每条各调一次 detail，结果缓存供点选复用）
      var slot = item.querySelector('[data-pc-slot]');
      fetchDetailCached(s.placeId).then(function (det) {
        if (token !== _addrReqToken || !slot) return;   // 已被更晚的搜索取代
        var pc = postcodeOf(det);
        slot.textContent = pc || '—';
        if (!pc) slot.classList.add('is-empty');
      });
    });
    el.hidden = false;
  }

  function pickAddress(s) {
    closeAddrSuggest();
    var pid = s && s.placeId; if (!pid) return;
    renderTfmAddrDetail({ loading: true });
    fetchDetailCached(pid).then(function (det) {
      if (!det) { renderTfmAddrDetail({ error: true }); return; }
      renderTfmAddrDetail({ detail: det, placeId: pid });
      var pc = postcodeOf(det);
      setHeroPcBadge(pc);   // 搜索框内显示解析出的邮编
      if (pc && typeof window.globalSearchPostcode === 'function') {
        window.globalSearchPostcode(pc);   // 用解析出的邮编跑原有搜索（框里仍保留地址文字）
      }
    });
  }

  // TFM 地址详情面板宿主集合：renderWhCard 在每张 TFM 家族卡（海运/大陆空运/香港空运）的
  // 「价格说明」上方各注入一个 .tfm-addr-detail 容器。三张表下面都展示同一份地址详情。
  function tfmAddrHosts() {
    var hosts = Array.prototype.slice.call(document.querySelectorAll('.tfm-addr-detail'));
    if (hosts.length) return hosts;
    // 兜底：卡片尚未渲染出容器时，在 #wh-card-tfm 后插入一个（保证不丢信息）
    var host = document.createElement('div');
    host.className = 'tfm-addr-detail';
    var anchor = $('wh-card-tfm');
    if (anchor) { anchor.insertAdjacentElement('afterend', host); return [host]; }
    var inline = $('whInline');
    if (inline) { inline.appendChild(host); return [host]; }
    return [];
  }

  function renderTfmAddrDetail(opts) {
    var hosts = tfmAddrHosts(); if (!hosts.length) return;
    var h;
    if (opts.loading) {
      h = '<div class="tfm-addr-title">地址详情（TFM）</div><div class="tfm-addr-body">查询中…</div>';
    } else if (opts.error) {
      h = '<div class="tfm-addr-title">地址详情（TFM）</div><div class="tfm-addr-body tfm-addr-err">地址详情获取失败，请重试</div>';
    } else {
      var det = opts.detail || {};
      var place = det.place || {};
      var parts = place.addressParts || {};
      var cls = det.classification || {};
      var addr = place.formattedAddress || (cls.addressValidation && cls.addressValidation.formattedAddress) || '';
      var isResidential = cls.residentialSurcharge === true || cls.kind === 'residential';
      var badge = isResidential
        ? '<span class="tfm-addr-badge tfm-addr-badge--res">住宅地址' + (cls.residentialSurcharge ? '（含住宅附加费）' : '') + '</span>'
        : '<span class="tfm-addr-badge tfm-addr-badge--biz">商业地址</span>';
      var meta = [];
      if (parts.suburb) meta.push('Suburb：' + esc(parts.suburb));
      if (parts.state) meta.push('State：' + esc(parts.state));
      if (parts.postcode) meta.push('邮编：' + esc(parts.postcode));
      var img = (det.image && opts.placeId)
        ? '<div class="tfm-addr-photo is-loading">'
          + '<div class="tfm-addr-photo-loading"><span class="tfm-spinner"></span>图片加载中…</div>'
          + '<img src="/api/address-image/' + encodeURIComponent(opts.placeId) + '?width=640" alt="建筑图片"'
          + ' onload="this.closest(\'.tfm-addr-photo\').classList.remove(\'is-loading\')"'
          + ' onerror="this.closest(\'.tfm-addr-photo\').style.display=\'none\'"></div>'
        : '';
      var info = '';
      info += '<div class="tfm-addr-row">' + badge + (place.displayName ? '<span class="tfm-addr-name">' + esc(place.displayName) + '</span>' : '') + '</div>';
      if (addr) info += '<div class="tfm-addr-full">' + esc(addr) + '</div>';
      if (meta.length) info += '<div class="tfm-addr-meta">' + meta.join('　') + '</div>';
      if (isResidential) info += '<div class="tfm-addr-fee">⚠ 私人住宅地址，加收 500 RMB/票 私人住宅费</div>';
      h = '<div class="tfm-addr-title">地址详情（TFM）</div>';
      h += '<div class="tfm-addr-2col">' + img + '<div class="tfm-addr-info">' + info + '</div></div>';
    }
    hosts.forEach(function (host) { host.innerHTML = h; });
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

  // 按 _index.json 动态重建海外仓卡片 + 渠道目录（含后台复制的副本）。返回是否成功重建。
  // 模板里预置了 4 张原始表作为兜底；索引拉取成功则用真实列表覆盖，副本随之出现。
  function buildWhListFromIndex(sheets) {
    var inline = $('whInline');
    if (!inline || !sheets) return false;   // 允许空数组：清空模板里硬编码的兜底卡片（全部隐藏时）
    inline.innerHTML = '';
    sheets.forEach(function (s) {
      if (!s || !s.key) return;
      var d = document.createElement('div');
      d.className = 'wh-card';
      d.id = 'wh-card-' + s.key;
      d.setAttribute('data-wh-key', s.key);
      inline.appendChild(d);
    });
    var navList = document.getElementById('csbWhNavList');
    if (navList) {
      navList.innerHTML = '';
      sheets.forEach(function (s) {
        if (!s || !s.key) return;
        var li = document.createElement('li');
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'channel-nav-link csb-wh-nav-item';
        btn.setAttribute('data-scroll-target', '#wh-card-' + s.key);
        btn.textContent = s.name || s.key;   // 卡片加载后 renderWhCard 会再同步一次名称
        li.appendChild(btn);
        navList.appendChild(li);
      });
    }
    WH_KEYS = sheets.map(function (s) { return s.key; }).filter(Boolean);
    return true;
  }

  // 渠道目录项点击 → 平滑定位到对应卡片（幂等绑定，动态重建后可重复调用）
  function wireWhNav() {
    document.querySelectorAll('.csb-wh-nav-item[data-scroll-target]').forEach(function (btn) {
      if (btn._whNavBound) return;
      btn._whNavBound = true;
      btn.addEventListener('click', function () {
        var target = document.querySelector(btn.getAttribute('data-scroll-target'));
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        document.querySelectorAll('.csb-wh-nav-item.active').forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
        // 移动端点选后收起浮动抽屉（与渠道目录一致）
        var panel = $('channelNavPanel');
        if (panel && window.matchMedia('(max-width: 768px)').matches) panel.removeAttribute('open');
      });
    });
  }

  function init() {
    var inlineEl = $('whInline');
    var whDir = (inlineEl && inlineEl.getAttribute('data-wh-dir')) || 'warehouse_au_dahuo';

    // 先按索引动态构建卡片 + 目录（含副本），完成后再拉取各表数据并绑定目录点击。
    // 索引拉取失败则沿用模板里硬编码的 4 张原始表，保证向后兼容不白屏。
    fetch('/api/warehouse-sheets?dir=' + encodeURIComponent(whDir))
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res && res.success && Array.isArray(res.data)) {
          // 「前台隐藏」的价格表不在文章展示：不建卡片、不建目录、不拉数据（全部隐藏时清空兜底卡片）
          var visible = res.data.filter(function (s) { return s && !s.hidden; });
          buildWhListFromIndex(visible);
        }
      })
      .catch(function () {})
      .then(function () {
        loadWarehouseTables();
        wireWhNav();
      });

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
      window.globalSearchPostcode = function (explicitCode) {
        var r = origSearch.apply(this, arguments);
        var forced = (typeof explicitCode === 'string' && /^\d{4}$/.test(explicitCode)) ? explicitCode : '';
        // 顶部批量互斥：有顶部值（或地址流传入的邮编）时清空左侧批量框
        if (forced || oneCode(($('globalPostcodeInput') || {}).value)) {
          if ($('batchPcInput')) $('batchPcInput').value = '';
          if ($('batchSummary')) $('batchSummary').innerHTML = '';
        }
        afterTopSearch(forced);
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

    // 顶部 hero「查询报价」按钮 & 输入框回车：邮编/地址分派
    var heroBtn = $('csbHeroQuery');
    if (heroBtn) heroBtn.addEventListener('click', csbHeroSubmit);
    var heroInput = $('globalPostcodeInput');
    if (heroInput) {
      heroInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); csbHeroSubmit(); }
        else if (e.key === 'Escape') { closeAddrSuggest(); removeTfmAddrDetail(); }
      });
    }
    // 清除 X：一并收起地址下拉与 TFM 详情面板
    var heroClear = $('globalSearchClearBtn');
    if (heroClear) heroClear.addEventListener('click', function () { closeAddrSuggest(); removeTfmAddrDetail(); _addrSession = ''; });
    // 点击搜索行以外处收起地址下拉（点输入框/查询按钮不收起）
    document.addEventListener('click', function (e) {
      var row = document.querySelector('.csb-hero-search-row');
      if (row && !row.contains(e.target)) closeAddrSuggest();
    });

    // 顶部「清空」按钮：清空所有查询态
    var clearAll = $('csbClearAll');
    if (clearAll) {
      clearAll.addEventListener('click', function () {
        var ti = $('globalPostcodeInput');
        if (ti) ti.value = '';
        closeAddrSuggest(); removeTfmAddrDetail(); _addrSession = '';
        if (typeof window.globalSearchPostcode === 'function') window.globalSearchPostcode();
        clearBatch();
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
