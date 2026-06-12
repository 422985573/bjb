/* eslint-disable no-unused-vars */
(function () {
  'use strict';

  var API_INDEX = '/api/warehouse-sheets';
  var API_SHEET = '/api/warehouse-sheet/';
  var API_POSTCODE_LOOKUP = '/api/warehouse-postcode-lookup';
  var PAGE_SIZE = 200;

  var sheetsIndex = [];
  var currentKey = '';
  var currentData = null;
  var zonePageNum = 1;
  var zoneSearchTerm = '';
  var sheetCache = {};
  var postcodeZoneMap = null;
  var postcodeZoneMaps = null;
  var autoNextLock = false;
  var bottomOverscroll = 0;
  var BOTTOM_OVERSCROLL_THRESHOLD = 120;

  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  function $$(sel, ctx) { return Array.from((ctx || document).querySelectorAll(sel)); }

  function init() {
    fetch(API_INDEX)
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.success) return;
        sheetsIndex = res.data || [];
        renderNav();
        if (sheetsIndex.length) loadSheet(sheetsIndex[0].key);
        bindAutoNextSheet();
      })
      .catch(function () {
        $('#whNavList').innerHTML = '<li class="wh-nav-item wh-nav-loading">加载失败</li>';
      });
  }

  function renderNav() {
    var ul = $('#whNavList');
    var tocList = $('#whTocList');
    ul.innerHTML = '';
    if (tocList) tocList.innerHTML = '';
    sheetsIndex.forEach(function (s) {
      var li = document.createElement('li');
      li.className = 'wh-nav-item';
      li.dataset.key = s.key;
      li.innerHTML = esc(s.name);
      if (s.row_count > 500) {
        li.innerHTML += ' <span class="wh-nav-rows">' + s.row_count + '</span>';
      }
      li.onclick = function () { loadSheet(s.key); };
      ul.appendChild(li);

      if (tocList) {
        var tli = document.createElement('li');
        tli.dataset.key = s.key;
        tli.textContent = s.name;
        tli.onclick = function () { loadSheet(s.key); closeTocPanel(); };
        tocList.appendChild(tli);
      }
    });
    bindTocPanel();
    bindGlobalPostcodeQuery();
  }

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
    fab.onclick = function () {
      overlay.classList.add('open');
      panel.classList.add('open');
    };
    overlay.onclick = closeTocPanel;
    if (closeBtn) closeBtn.onclick = closeTocPanel;
  }

  function setActiveNav(key) {
    $$('.wh-nav-item').forEach(function (el) {
      el.classList.toggle('active', el.dataset.key === key);
    });
    $$('.wh-toc-list li').forEach(function (el) {
      el.classList.toggle('active', el.dataset.key === key);
    });
  }

  function loadSheet(key) {
    if (key === currentKey && currentData) return;
    currentKey = key;
    zonePageNum = 1;
    zoneSearchTerm = '';
    bottomOverscroll = 0;
    setActiveNav(key);

    if (sheetCache[key]) {
      currentData = sheetCache[key];
      renderSheet();
      return;
    }

    var content = $('#whContent');
    content.innerHTML = '<div class="wh-loading" id="whLoading"><div class="wh-spinner"></div><span>加载中...</span></div>';

    fetch(API_SHEET + encodeURIComponent(key))
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.success) {
          content.innerHTML = '<p style="color:#ef4444;padding:20px;">加载失败</p>';
          return;
        }
        currentData = res.data;
        sheetCache[key] = currentData;
        renderSheet();
      })
      .catch(function () {
        content.innerHTML = '<p style="color:#ef4444;padding:20px;">网络错误</p>';
      });
  }

  function renderSheet() {
    if (!currentData) return;
    postcodeZoneMap = currentData.postcode_zone_map || null;
    postcodeZoneMaps = currentData.postcode_zone_maps || null;
    var html = '<h2 class="wh-sheet-title">' + esc(currentData.name) + '</h2>';

    if (postcodeZoneMap) {
      html += renderPostcodeQueryBox('whPostcodeInput', 'whPostcodeResult');
    }

    var sections = currentData.sections || [];

    sections.forEach(function (sec, si) {
      html += renderSection(sec, si);
    });

    html += renderNextSheetHint();

    $('#whContent').innerHTML = html;

    $$('.wh-search-input').forEach(function (inp) {
      inp.addEventListener('input', debounce(function () {
        zoneSearchTerm = inp.value.trim().toLowerCase();
        zonePageNum = 1;
        rerenderZoneTable(inp.closest('.wh-section'));
      }, 200));
    });

    $$('.wh-page-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var dir = parseInt(btn.dataset.dir);
        zonePageNum += dir;
        if (zonePageNum < 1) zonePageNum = 1;
        rerenderZoneTable(btn.closest('.wh-section'));
      });
    });

    $$('.wh-nav-link-item').forEach(function (el) {
      el.addEventListener('click', function () {
        var targetKey = el.dataset.key;
        if (targetKey) loadSheet(targetKey);
      });
    });

    bindPostcodeQuery();
    bindPerSectionPostcodeQuery();
  }

  function renderSection(sec, si) {
    var h = '<div class="wh-section" data-section-idx="' + si + '">';

    if (sec.title) {
      h += '<div class="wh-section-title">' + esc(sec.title) + '</div>';
    }

    if (sec.type === 'info') {
      h += '<ul class="wh-info-list">';
      (sec.rows || []).forEach(function (r) {
        h += '<li>' + esc(r[0]) + '</li>';
      });
      h += '</ul>';
    } else if (sec.type === 'nav') {
      h += '<ul class="wh-nav-links">';
      (sec.rows || []).forEach(function (r) {
        var name = r[0] || '';
        var key = findSheetKeyByName(name);
        h += '<li class="wh-nav-link-item"' + (key ? ' data-key="' + esc(key) + '"' : '') + '>' + esc(name) + '</li>';
      });
      h += '</ul>';
    } else if (sec.type === 'richtext') {
      h += '<div class="wh-richtext">' + (sec.html || '') + '</div>';
    } else if (sec.type === 'zone_table') {
      // Hide zone_table on display page when postcode query is available
      if (postcodeZoneMap || postcodeZoneMaps) {
        return '';
      }
      h += renderZoneTableSection(sec, si);
    } else {
      if (hasMarkerGroups(sec.rows || [])) {
        h += renderMarkerGroupedTableSection(sec);
      } else {
        h += renderTableSection(sec);
      }

      // Per-section postcode query box (for sheets with separate maps per warehouse)
      if (postcodeZoneMaps && sec.type === 'price_table') {
        var mapKey = getMapKeyFromTitle(sec.title || '');
        if (mapKey && postcodeZoneMaps[mapKey]) {
          h += renderPostcodeQueryBox('whPcInput_' + si, 'whPcResult_' + si, mapKey, si);
        }
      }
    }

    if (sec.notes && sec.notes.length) {
      h += '<div class="wh-section-notes">';
      sec.notes.forEach(function (n) { h += '<p>' + esc(n) + '</p>'; });
      h += '</div>';
    }

    h += '</div>';
    return h;
  }

  function isFullSpanRow(row, colCount) {
    if (colCount < 5) return false;
    var filled = 0;
    var leadingEmpty = 0;
    var seenFilled = false;
    for (var i = 0; i < row.length; i++) {
      var v = cellValue(row[i]);
      if (v !== '' && v !== null && v !== undefined) {
        seenFilled = true;
        filled += cellColspan(row[i]);
      } else if (!seenFilled) {
        leadingEmpty++;
      }
    }
    if (leadingEmpty > 1) return false;
    return filled > 0 && filled * 3 <= colCount;
  }

  function cellValue(c) {
    if (c && typeof c === 'object' && !Array.isArray(c)) {
      return c.v != null ? c.v : (c.text != null ? c.text : '');
    }
    return c;
  }

  function cellColspan(c) {
    if (c && typeof c === 'object' && !Array.isArray(c)) {
      return c.cs || c.colspan || 1;
    }
    return 1;
  }

  function buildColspanCover(rows) {
    var covered = {};
    for (var ri = 0; ri < rows.length; ri++) {
      for (var ci = 0; ci < rows[ri].length; ci++) {
        var cs = cellColspan(rows[ri][ci]);
        if (cs > 1) {
          for (var k = 1; k < cs; k++) covered[ri + '_' + (ci + k)] = true;
        }
      }
    }
    return covered;
  }

  function isMarkerRow(row) {
    if (!row[0] || row[0] === '') return false;
    for (var i = 1; i < row.length; i++) {
      if (row[i] !== '' && row[i] !== null && row[i] !== undefined) return false;
    }
    return true;
  }

  function hasMarkerGroups(rows) {
    if (rows.length < 3) return false;
    var markers = 0;
    for (var i = 0; i < rows.length; i++) {
      if (isMarkerRow(rows[i])) markers++;
    }
    return markers >= 2 && markers <= rows.length / 3;
  }

  function renderMarkerGroupedTableSection(sec) {
    var rows = sec.rows || [];
    var headers = sec.headers || [];
    var groups = [];
    var cur = null;
    for (var i = 0; i < rows.length; i++) {
      if (isMarkerRow(rows[i])) {
        cur = {state: rows[i][0], start: i + 1, end: i};
        groups.push(cur);
      } else if (cur) {
        cur.end = i;
      }
    }

    var h = '<div class="wh-table-wrap"><table class="wh-table"><thead><tr><th>State</th>';
    headers.forEach(function (th) { h += '<th>' + esc(th) + '</th>'; });
    h += '</tr></thead><tbody>';

    groups.forEach(function (g) {
      var cnt = g.end - g.start + 1;
      if (cnt < 1) return;
      for (var ri = g.start; ri <= g.end; ri++) {
        h += '<tr>';
        if (ri === g.start) {
          h += '<td rowspan="' + cnt + '" style="vertical-align:middle;font-weight:700;background:#f8fafc;border-right:2px solid #dbeafe;">' + esc(g.state) + '</td>';
        }
        rows[ri].forEach(function (c) {
          h += '<td>' + esc(c) + '</td>';
        });
        h += '</tr>';
      }
    });

    h += '</tbody></table></div>';
    return h;
  }

  function renderTableSection(sec) {
    var rows = sec.rows || [];
    var headers = sec.headers || [];
    if (!rows.length && !headers.length) return '';

    var colCount = rows.length ? rows[0].length : headers.length;

    var mergeMap = buildRowspanMap(rows);

    var h = '<div class="wh-table-wrap"><table class="wh-table"><thead><tr>';
    headers.forEach(function (th) {
      h += '<th>' + esc(th) + '</th>';
    });
    h += '</tr></thead><tbody>';

    rows.forEach(function (row, ri) {
      if (isFullSpanRow(row, colCount)) {
        var parts = [];
        row.forEach(function (c) {
          var v = cellValue(c);
          if (v !== '' && v !== null && v !== undefined) parts.push(String(v));
        });
        h += '<tr><td colspan="' + colCount + '" class="wh-cell-note">' + esc(parts.join('  ')) + '</td></tr>';
      } else {
        h += '<tr>';
        var ci = 0;
        while (ci < row.length) {
          var rawCell = row[ci];
          var m = mergeMap[ri + '_' + ci];
          if (m === 0) { ci++; continue; }
          var value = cellValue(rawCell);
          var cs = cellColspan(rawCell);
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
          if (prevV !== '' && prevV !== null && prevV !== undefined) {
            hasGrouping = true;
            break;
          }
        }
      }
      if (!hasGrouping) continue;

      var anchor = -1;
      for (var ri = 0; ri < rows.length; ri++) {
        if (covered[ri + '_' + ci]) {
          if (anchor >= 0) map[anchor + '_' + ci] = ri - anchor;
          anchor = -1;
          continue;
        }
        var v = cellValue(rows[ri][ci]);
        var isEmpty = (v === '' || v === null || v === undefined);
        var rowIsFullSpan = isFullSpanRow(rows[ri], cols);
        if (rowIsFullSpan) {
          if (anchor >= 0) map[anchor + '_' + ci] = ri - anchor;
          anchor = -1;
          continue;
        }
        if (!isEmpty) {
          if (anchor >= 0) map[anchor + '_' + ci] = ri - anchor;
          anchor = ri;
          map[ri + '_' + ci] = 1;
        } else {
          if (anchor >= 0) map[ri + '_' + ci] = 0;
        }
      }
      if (anchor >= 0) map[anchor + '_' + ci] = rows.length - anchor;
    }
    return map;
  }

  function renderZoneTableSection(sec, si) {
    var allRows = sec.rows || [];
    var headers = sec.headers || [];
    if (!allRows.length) return '';

    var filtered = allRows;
    if (zoneSearchTerm) {
      filtered = allRows.filter(function (r) {
        return r.some(function (c) { return String(c).toLowerCase().indexOf(zoneSearchTerm) >= 0; });
      });
    }

    var totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    if (zonePageNum > totalPages) zonePageNum = totalPages;
    var start = (zonePageNum - 1) * PAGE_SIZE;
    var pageRows = filtered.slice(start, start + PAGE_SIZE);

    var h = '<div class="wh-search-bar">';
    h += '<input type="text" class="wh-search-input" placeholder="搜索邮编或区域..." value="' + esc(zoneSearchTerm) + '">';
    h += '<span class="wh-search-count">' + filtered.length + ' 条结果</span>';
    h += '</div>';

    h += '<div class="wh-table-wrap wh-zone-table" data-section-idx="' + si + '"><table class="wh-table"><thead><tr>';
    headers.forEach(function (th) { h += '<th>' + esc(th) + '</th>'; });
    h += '</tr></thead><tbody>';
    pageRows.forEach(function (row) {
      h += '<tr>';
      row.forEach(function (c) { h += '<td>' + esc(c) + '</td>'; });
      h += '</tr>';
    });
    h += '</tbody></table></div>';

    if (totalPages > 1) {
      h += '<div class="wh-pagination">';
      h += '<button class="wh-page-btn" data-dir="-1"' + (zonePageNum <= 1 ? ' disabled' : '') + '>上一页</button>';
      h += '<span>' + zonePageNum + ' / ' + totalPages + '</span>';
      h += '<button class="wh-page-btn" data-dir="1"' + (zonePageNum >= totalPages ? ' disabled' : '') + '>下一页</button>';
      h += '</div>';
    }

    return h;
  }

  function rerenderZoneTable(sectionEl) {
    if (!sectionEl || !currentData) return;
    var si = parseInt(sectionEl.dataset.sectionIdx);
    var sec = (currentData.sections || [])[si];
    if (!sec || sec.type !== 'zone_table') return;

    var searchVal = '';
    var inp = sectionEl.querySelector('.wh-search-input');
    if (inp) searchVal = inp.value.trim().toLowerCase();
    zoneSearchTerm = searchVal;

    var newHtml = renderZoneTableSection(sec, si);
    var temp = document.createElement('div');
    temp.className = 'wh-section';
    temp.dataset.sectionIdx = si;
    if (sec.title) {
      temp.innerHTML = '<div class="wh-section-title">' + esc(sec.title) + '</div>' + newHtml;
    } else {
      temp.innerHTML = newHtml;
    }
    sectionEl.parentNode.replaceChild(temp, sectionEl);

    var newInp = temp.querySelector('.wh-search-input');
    if (newInp) {
      newInp.focus();
      newInp.setSelectionRange(newInp.value.length, newInp.value.length);
      newInp.addEventListener('input', debounce(function () {
        zoneSearchTerm = newInp.value.trim().toLowerCase();
        zonePageNum = 1;
        rerenderZoneTable(temp);
      }, 200));
    }

    temp.querySelectorAll('.wh-page-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        zonePageNum += parseInt(btn.dataset.dir);
        if (zonePageNum < 1) zonePageNum = 1;
        rerenderZoneTable(temp);
      });
    });
  }

  function getMapKeyFromTitle(title) {
    if (title.indexOf('悉尼') >= 0) return 'sydney';
    if (title.indexOf('墨尔本') >= 0) return 'melbourne';
    return '';
  }

  function renderPostcodeQueryBox(inputId, resultId, mapKey, sectionIdx) {
    var h = '<div class="wh-postcode-query"';
    if (mapKey) {
      h += ' data-map-key="' + esc(mapKey) + '" data-section-idx="' + sectionIdx + '"';
    }
    h += '>';
    h += '<label class="wh-postcode-label">邮编查询</label>';
    h += '<input type="text" class="wh-postcode-input" id="' + inputId + '" placeholder="输入4位邮编" maxlength="4" inputmode="numeric">';
    h += '<span class="wh-postcode-result" id="' + resultId + '"></span>';
    h += '</div>';
    return h;
  }

  function bindPostcodeQuery() {
    var inp = document.getElementById('whPostcodeInput');
    if (!inp) return;

    var timer = null;
    inp.addEventListener('input', function () {
      var raw = inp.value.replace(/[^0-9]/g, '').slice(0, 4);
      if (inp.value !== raw) inp.value = raw;

      if (timer) clearTimeout(timer);
      timer = setTimeout(function () {
        doPostcodeLookup(raw);
      }, 200);
    });

    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        inp.value = '';
        doPostcodeLookup('');
      }
    });
  }

  function bindPerSectionPostcodeQuery() {
    if (!postcodeZoneMaps) return;
    $$('.wh-postcode-query[data-map-key]').forEach(function (box) {
      var mapKey = box.dataset.mapKey;
      var si = parseInt(box.dataset.sectionIdx);
      var inp = box.querySelector('.wh-postcode-input');
      var resultEl = box.querySelector('.wh-postcode-result');
      if (!inp) return;

      var timer = null;
      inp.addEventListener('input', function () {
        var raw = inp.value.replace(/[^0-9]/g, '').slice(0, 4);
        if (inp.value !== raw) inp.value = raw;

        if (timer) clearTimeout(timer);
        timer = setTimeout(function () {
          doPerSectionLookup(raw, mapKey, si, resultEl);
        }, 200);
      });

      inp.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
          inp.value = '';
          doPerSectionLookup('', mapKey, si, resultEl);
        }
      });
    });
  }

  function doPerSectionLookup(code, mapKey, sectionIdx, resultEl) {
    // Clear highlights only within this section
    var sectionEl = document.querySelector('.wh-section[data-section-idx="' + sectionIdx + '"]');
    if (sectionEl) {
      sectionEl.querySelectorAll('.wh-table tbody tr.wh-row-highlight').forEach(function (tr) {
        tr.classList.remove('wh-row-highlight');
      });
    }

    if (!code || code.length < 3 || !postcodeZoneMaps || !postcodeZoneMaps[mapKey]) {
      if (resultEl) {
        resultEl.textContent = '';
        resultEl.className = 'wh-postcode-result';
      }
      return;
    }

    var zone = postcodeZoneMaps[mapKey][code];
    if (!zone) {
      if (resultEl) {
        resultEl.textContent = '未找到该邮编对应分区';
        resultEl.className = 'wh-postcode-result wh-postcode-error';
      }
      return;
    }

    if (resultEl) {
      resultEl.textContent = '分区：' + zone;
      resultEl.className = 'wh-postcode-result wh-postcode-ok';
    }

    // Highlight matching rows only within this section
    if (!sectionEl) return;
    var firstMatch = null;
    sectionEl.querySelectorAll('.wh-table tbody tr').forEach(function (tr) {
      var cells = tr.querySelectorAll('td');
      var matched = false;
      for (var i = 0; i < cells.length; i++) {
        if (cells[i].textContent.trim() === zone) {
          matched = true;
          break;
        }
      }
      if (matched) {
        tr.classList.add('wh-row-highlight');
        if (!firstMatch) firstMatch = tr;
      }
    });

    if (firstMatch) {
      firstMatch.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  function doPostcodeLookup(code) {
    var result = document.getElementById('whPostcodeResult');
    // Clear all highlights
    $$('.wh-table tbody tr.wh-row-highlight').forEach(function (tr) {
      tr.classList.remove('wh-row-highlight');
    });

    if (!code || code.length < 3 || !postcodeZoneMap) {
      if (result) {
        result.textContent = '';
        result.className = 'wh-postcode-result';
      }
      return;
    }

    var zone = postcodeZoneMap[code];
    if (!zone) {
      if (result) {
        result.textContent = '未找到该邮编对应分区';
        result.className = 'wh-postcode-result wh-postcode-error';
      }
      return;
    }

    if (result) {
      result.textContent = '分区：' + zone;
      result.className = 'wh-postcode-result wh-postcode-ok';
    }

    // Highlight matching rows in all price tables
    var firstMatch = null;
    $$('.wh-table tbody tr').forEach(function (tr) {
      var cells = tr.querySelectorAll('td');
      var matched = false;
      for (var i = 0; i < cells.length; i++) {
        if (cells[i].textContent.trim() === zone) {
          matched = true;
          break;
        }
      }
      if (matched) {
        tr.classList.add('wh-row-highlight');
        if (!firstMatch) firstMatch = tr;
      }
    });

    // Scroll to the first highlighted row
    if (firstMatch) {
      firstMatch.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  function findSheetKeyByName(name) {
    var cleaned = name.replace(/\s+/g, '').toLowerCase();
    for (var i = 0; i < sheetsIndex.length; i++) {
      var s = sheetsIndex[i];
      if (s.name.replace(/\s+/g, '').toLowerCase().indexOf(cleaned) >= 0 ||
          cleaned.indexOf(s.name.replace(/\s+/g, '').toLowerCase()) >= 0) {
        return s.key;
      }
    }
    return '';
  }

  function esc(v) {
    if (v === null || v === undefined) return '';
    var s = String(v);
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function debounce(fn, ms) {
    var t;
    return function () {
      clearTimeout(t);
      var args = arguments;
      var ctx = this;
      t = setTimeout(function () { fn.apply(ctx, args); }, ms);
    };
  }

  function toggleNavVisibility(hasResults) {
    var navList = $('#whNavList');
    var tocList = $('#whTocList');
    if (navList) navList.style.display = hasResults ? 'none' : '';
    if (tocList) tocList.style.display = hasResults ? 'none' : '';
  }

  function bindGlobalPostcodeQuery() {
    var desktopInput = document.getElementById('whGlobalPcInput');
    var mobileInput = document.getElementById('whGlobalPcInputMobile');
    var desktopResults = document.getElementById('whGlobalPcResults');
    var mobileResults = document.getElementById('whGlobalPcResultsMobile');

    function setupInput(inp, resultsEl) {
      if (!inp) return;
      var clearBtn = inp.parentNode.querySelector('.wh-pc-clear');
      function updateClearBtn() {
        if (clearBtn) clearBtn.style.display = inp.value ? '' : 'none';
      }
      updateClearBtn();
      var doLookup = debounce(function () {
        var raw = inp.value.replace(/[^0-9]/g, '').slice(0, 4);
        if (inp.value !== raw) inp.value = raw;
        updateClearBtn();
        globalPostcodeLookup(raw, resultsEl);
      }, 300);
      inp.addEventListener('input', doLookup);
      inp.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
          inp.value = '';
          updateClearBtn();
          globalPostcodeLookup('', resultsEl);
        }
      });
      if (clearBtn) {
        clearBtn.addEventListener('click', function () {
          inp.value = '';
          updateClearBtn();
          globalPostcodeLookup('', resultsEl);
          inp.focus();
        });
      }
    }

    setupInput(desktopInput, desktopResults);
    setupInput(mobileInput, mobileResults);
  }

  function globalPostcodeLookup(code, resultsEl) {
    if (!resultsEl) return;
    if (!code || code.length < 3) {
      resultsEl.innerHTML = '';
      toggleNavVisibility(false);
      return;
    }

    fetch(API_POSTCODE_LOOKUP + '?code=' + encodeURIComponent(code))
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.success || !res.data || !res.data.length) {
          resultsEl.innerHTML = '<div class="wh-pc-no-result">未找到该邮编</div>';
          toggleNavVisibility(true);
          return;
        }
        var html = '<div class="wh-pc-results">';
        res.data.forEach(function (item) {
          var label = item.name;
          if (item.warehouse) label += ' (' + item.warehouse + ')';
          html += '<div class="wh-pc-result-item" data-key="' + esc(item.key) + '">';
          html += '<span class="wh-pc-result-name">' + esc(label) + '</span>';
          html += '<span class="wh-pc-zone-badge">' + esc(item.zone) + '</span>';
          html += '</div>';
        });
        html += '</div>';
        resultsEl.innerHTML = html;
        toggleNavVisibility(true);

        resultsEl.querySelectorAll('.wh-pc-result-item').forEach(function (el) {
          el.addEventListener('click', function () {
            loadSheet(el.dataset.key);
            closeTocPanel();
          });
        });
      })
      .catch(function () {
        resultsEl.innerHTML = '<div class="wh-pc-no-result">查询失败</div>';
      });
  }

  function renderNextSheetHint() {
    if (!sheetsIndex.length || !currentKey) return '';
    var idx = -1;
    for (var i = 0; i < sheetsIndex.length; i++) {
      if (sheetsIndex[i].key === currentKey) { idx = i; break; }
    }
    if (idx < 0 || idx >= sheetsIndex.length - 1) return '';
    var nextName = sheetsIndex[idx + 1].name;
    return '<div class="wh-next-hint">继续向下滑动切换到「' + esc(nextName) + '」</div>';
  }

  function bindAutoNextSheet() {
    var contentEl = $('#whContent');
    if (!contentEl) return;
    var lastTouchY = null;

    function isDesktopScroll() {
      return window.innerWidth > 768;
    }

    function isAtBottom() {
      if (isDesktopScroll()) {
        return contentEl.scrollHeight - (contentEl.scrollTop + contentEl.clientHeight) <= 4;
      }
      var scrollY = window.scrollY || window.pageYOffset || document.documentElement.scrollTop;
      var viewport = window.innerHeight;
      var docHeight = Math.max(
        document.documentElement.scrollHeight,
        document.body.scrollHeight
      );
      return docHeight - (scrollY + viewport) <= 4;
    }

    function findNextKey() {
      if (!sheetsIndex.length || !currentKey) return null;
      var idx = -1;
      for (var i = 0; i < sheetsIndex.length; i++) {
        if (sheetsIndex[i].key === currentKey) { idx = i; break; }
      }
      if (idx < 0 || idx >= sheetsIndex.length - 1) return null;
      return sheetsIndex[idx + 1].key;
    }

    function triggerNext() {
      var nextKey = findNextKey();
      if (!nextKey) return false;
      autoNextLock = true;
      bottomOverscroll = 0;
      loadSheet(nextKey);
      setTimeout(function () {
        if (isDesktopScroll()) {
          contentEl.scrollTop = 0;
        } else {
          window.scrollTo({ top: 0, behavior: 'auto' });
        }
        setTimeout(function () { autoNextLock = false; }, 400);
      }, 80);
      return true;
    }

    function inContent(target) {
      return target && contentEl.contains(target);
    }

    function onWheel(e) {
      if (autoNextLock) return;
      if (e.deltaY <= 0) { bottomOverscroll = 0; return; }
      if (isDesktopScroll() && !inContent(e.target)) { bottomOverscroll = 0; return; }
      if (!isAtBottom()) { bottomOverscroll = 0; return; }
      bottomOverscroll += e.deltaY;
      if (bottomOverscroll >= BOTTOM_OVERSCROLL_THRESHOLD) triggerNext();
    }

    function onTouchStart(e) {
      lastTouchY = e.touches && e.touches[0] ? e.touches[0].clientY : null;
    }

    function onTouchMove(e) {
      if (autoNextLock) return;
      if (lastTouchY === null || !e.touches || !e.touches[0]) return;
      var curY = e.touches[0].clientY;
      var dy = lastTouchY - curY;
      lastTouchY = curY;
      if (dy <= 0) { bottomOverscroll = 0; return; }
      if (isDesktopScroll() && !inContent(e.target)) { bottomOverscroll = 0; return; }
      if (!isAtBottom()) { bottomOverscroll = 0; return; }
      bottomOverscroll += Math.min(dy, 40);
      if (bottomOverscroll >= BOTTOM_OVERSCROLL_THRESHOLD) triggerNext();
    }

    function onTouchEnd() { lastTouchY = null; }

    window.addEventListener('wheel', onWheel, { passive: true });
    window.addEventListener('touchstart', onTouchStart, { passive: true });
    window.addEventListener('touchmove', onTouchMove, { passive: true });
    window.addEventListener('touchend', onTouchEnd, { passive: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
