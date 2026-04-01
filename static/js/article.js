// 顶部邮编查询：请求防抖/防乱序
let GLOBAL_POSTCODE_QUERY_TOKEN = 0;
// 渠道目录程序化滚动保护期：避免 sticky 克隆表头抢占视觉定位
let IS_CHANNEL_NAV_AUTO_SCROLLING = false;
let CHANNEL_NAV_AUTO_SCROLL_TIMERS = [];
let CHANNEL_NAV_SCROLL_SESSION = 0;
let CHANNEL_NAV_ACTIVE_SCROLLEND_HANDLER = null;
let GLOBAL_SEARCH_TIMER = null;
const GLOBAL_SEARCH_DEBOUNCE_MS = 220;
const POSTCODE_FETCH_TIMEOUT_MS = 20000;
const SCROLL_NUDGE_PX = 0;
const AUTO_SCROLL_FALLBACK_MS = 2600;
const STICKY_SUPPRESS_AFTER_NAV_MS = 900;
const FINAL_SETTLE_EPS_PX = 3;
const STICKY_RESUME_DELAY_MS = 100;
let LAST_STICKY_REFRESH = function() {};
let STICKY_RUNTIME = null;
let STICKY_SUPPRESS_UNTIL = 0;

function toAsciiDigitsOnly(raw) {
    const text = String(raw || '');
    let out = '';
    for (const ch of text) {
        const code = ch.charCodeAt(0);
        // 全角数字转半角
        if (code >= 0xFF10 && code <= 0xFF19) {
            out += String.fromCharCode(code - 0xFEE0);
            continue;
        }
        // 仅保留 0-9
        if (code >= 48 && code <= 57) {
            out += ch;
        }
    }
    return out;
}

function extractFourDigitPostcode(raw) {
    return toAsciiDigitsOnly(raw).slice(0, 4);
}

function clearChannelNavAutoScrollTimers() {
    CHANNEL_NAV_AUTO_SCROLL_TIMERS.forEach(function(timerId) {
        clearTimeout(timerId);
    });
    CHANNEL_NAV_AUTO_SCROLL_TIMERS = [];
}

function globalSearchPostcodeDebounced() {
    if (GLOBAL_SEARCH_TIMER) {
        clearTimeout(GLOBAL_SEARCH_TIMER);
    }
    GLOBAL_SEARCH_TIMER = setTimeout(function() {
        globalSearchPostcode();
    }, GLOBAL_SEARCH_DEBOUNCE_MS);
}

async function fetchJsonWithRetry(url, options, maxRetries) {
    const retries = typeof maxRetries === 'number' ? maxRetries : 2;
    let lastError = null;
    const timeoutMs = options && typeof options.timeoutMs === 'number' ? options.timeoutMs : 0;
    const mergedOptions = Object.assign(
        {
            cache: 'no-store',
            headers: {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache'
            }
        },
        options || {}
    );
    delete mergedOptions.timeoutMs;
    const incomingHeaders = (options && options.headers) ? options.headers : {};
    mergedOptions.headers = Object.assign({}, mergedOptions.headers || {}, incomingHeaders);
    for (let attempt = 0; attempt <= retries; attempt++) {
        let timeoutId = null;
        try {
            if (timeoutMs > 0) {
                const controller = new AbortController();
                mergedOptions.signal = controller.signal;
                timeoutId = window.setTimeout(function() {
                    controller.abort(new Error('REQUEST_TIMEOUT'));
                }, timeoutMs);
            } else {
                delete mergedOptions.signal;
            }
            const response = await fetch(url, mergedOptions);
            if (!response.ok) {
                throw new Error('HTTP ' + response.status);
            }
            return await response.json();
        } catch (error) {
            lastError = error;
            if (attempt >= retries) break;
            // 轻量退避，减少瞬时网络抖动对搜索结果的影响
            await new Promise(resolve => setTimeout(resolve, 180 * (attempt + 1)));
        } finally {
            if (timeoutId !== null) {
                clearTimeout(timeoutId);
            }
        }
    }
    throw lastError || new Error('fetch failed');
}

async function fetchPostcodeEvaluate(postcode, shouldAbort) {
    if (typeof shouldAbort === 'function' && shouldAbort()) {
        return { aborted: true, data: null, error: null };
    }
    try {
        const data = await fetchJsonWithRetry('/api/postcode-evaluate/' + encodeURIComponent(postcode), {
            timeoutMs: POSTCODE_FETCH_TIMEOUT_MS
        }, 0);
        return { aborted: false, data: data, error: null };
    } catch (error) {
        return { aborted: false, data: null, error: error };
    }
}

function getPostcodeEvaluateMessage(evaluateData, fetchError) {
    if (fetchError) {
        if (fetchError.name === 'AbortError') {
            return '网络不佳，请稍后重试';
        }
        return '查询服务暂时不可用，请稍后再试';
    }
    const status = ((evaluateData && evaluateData.status) || '').toString().toLowerCase();
    if (status === 'no_service') {
        return '无服务';
    }
    if (status === 'unavailable') {
        return evaluateData.message || '查询服务暂时不可用，请稍后再试';
    }
    if (status === 'error') {
        return evaluateData.message || '查询失败，请稍后再试';
    }
    return '查询服务暂时不可用，请稍后再试';
}

function buildChannelServicePrefix(dajianStatus, zhixiangStatus) {
    const dajianNoService = dajianStatus === 'no_service';
    const zhixiangNoService = zhixiangStatus === 'no_service';
    const dajianOk = dajianStatus === 'ok';
    const zhixiangOk = zhixiangStatus === 'ok';
    if (dajianNoService && zhixiangNoService) return '大件/纸箱无服务';
    if (dajianNoService && zhixiangOk) return '大件无服务，纸箱有服务';
    if (dajianOk && zhixiangNoService) return '纸箱无服务，大件有服务';
    return '';
}

function hasValidDistance(distance) {
    return distance !== null && distance !== undefined && !Number.isNaN(Number(distance));
}

function normalizeDistanceUnit(unit) {
    const unitRaw = (unit || '').toString().toLowerCase();
    return (unitRaw === 'km' || unitRaw === '公里') ? '公里' : (unit || '');
}

function getCurrentNavHeight() {
    const navEl = document.querySelector('.main-nav');
    return navEl ? navEl.getBoundingClientRect().height : 60;
}

function syncNavHeightVar() {
    const navHeight = Math.round(getCurrentNavHeight());
    document.documentElement.style.setProperty('--nav-height', navHeight + 'px');
}

function shouldUseClonedStickyHeaders() {
    return true;
}

function initArticlePage() {
    syncNavHeightVar();
    highlightChannelNames();
    mergeAllChannelTables();
    formatStandardCellText();
    setupIOSInputZoomGuard();
    setupGlobalSearchClearButton();
    setupPostcodePopover();
    setupChannelNavigator();
    setupTableRowHover();
    setupStickyTableHeaders();
    setupImageLightbox();
    setupBackToTop();
}

// 渠道表格：自动合并相同目的港/邮编/到门时效/渠道单元格
document.addEventListener('DOMContentLoaded', function() {
    initArticlePage();
});

function setupIOSInputZoomGuard() {
    const viewportMeta = document.querySelector('meta[name="viewport"]');
    if (!viewportMeta) return;

    const ua = navigator.userAgent || '';
    const isIOSDevice = /iPhone|iPad|iPod/i.test(ua)
        || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    if (!isIOSDevice) return;

    const focusSelector = [
        '#globalPostcodeInput',
        '.postcode-input'
    ].join(',');

    const originalViewport = viewportMeta.getAttribute('content') || '';
    let viewportLocked = false;
    let restoreTimer = null;

    const lockViewport = function() {
        if (viewportLocked) return;
        viewportMeta.setAttribute(
            'content',
            'width=device-width, initial-scale=1.0, minimum-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover'
        );
        viewportLocked = true;
    };

    const restoreViewport = function() {
        if (!viewportLocked) return;
        viewportMeta.setAttribute('content', originalViewport);
        viewportLocked = false;
    };

    document.addEventListener('focusin', function(event) {
        const target = event.target;
        if (!(target instanceof HTMLElement) || !target.matches(focusSelector)) return;
        if (restoreTimer) {
            clearTimeout(restoreTimer);
            restoreTimer = null;
        }
        lockViewport();
    });

    document.addEventListener('focusout', function(event) {
        const target = event.target;
        if (!(target instanceof HTMLElement) || !target.matches(focusSelector)) return;
        if (restoreTimer) {
            clearTimeout(restoreTimer);
        }
        restoreTimer = window.setTimeout(function() {
            const activeEl = document.activeElement;
            if (activeEl instanceof HTMLElement && activeEl.matches(focusSelector)) {
                return;
            }
            restoreViewport();
            restoreTimer = null;
        }, 180);
    });
}

function setupGlobalSearchClearButton() {
    const input = document.getElementById('globalPostcodeInput');
    const clearBtn = document.getElementById('globalSearchClearBtn');
    if (!input || !clearBtn) return;

    const normalizeInputValue = function() {
        const normalized = extractFourDigitPostcode(input.value);
        if (input.value !== normalized) {
            input.value = normalized;
        }
    };

    const syncClearBtn = function() {
        const hasValue = (input.value || '').length > 0;
        clearBtn.hidden = !hasValue;
    };

    clearBtn.addEventListener('click', function() {
        input.value = '';
        syncClearBtn();
        if (typeof window.globalSearchPostcode === 'function') {
            window.globalSearchPostcode();
        }
        input.focus();
    });

    input.addEventListener('input', function() {
        normalizeInputValue();
        syncClearBtn();
    });
    input.addEventListener('beforeinput', function(e) {
        // 顶部搜索只允许 4 位数字，统一输入行为（含法/全角输入）
        if (e.inputType !== 'insertText' || !e.data) return;
        const incoming = extractFourDigitPostcode(e.data);
        if (!incoming) {
            e.preventDefault();
        }
    });
    input.addEventListener('paste', function(e) {
        const pasted = e.clipboardData ? e.clipboardData.getData('text') : '';
        const normalized = extractFourDigitPostcode(pasted);
        e.preventDefault();
        input.value = normalized;
        syncClearBtn();
        globalSearchPostcodeDebounced();
    });
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && input.value) {
            input.value = '';
            syncClearBtn();
            if (typeof window.globalSearchPostcode === 'function') {
                window.globalSearchPostcode();
            }
        }
    });

    syncClearBtn();
}

// 将“1、2、3...”规则文本自动换行，提升可读性
function formatStandardCellText() {
    document.querySelectorAll('.channel-table td.standard-cell, .channel-table td.col-dynamic.col-standard').forEach(function(cell) {
        const raw = (cell.textContent || '').trim();
        if (!raw || !/\d+、/.test(raw)) return;
        const formatted = raw.replace(/\s*(\d+、)/g, function(match, marker, offset) {
            return offset === 0 ? marker : '\n' + marker;
        });
        cell.textContent = formatted;
    });
}

function isDynamicWeightColumn(colIndex, totalColumns) {
    return colIndex >= 3 && colIndex <= totalColumns - 3;
}

function detectColumnRoleByHeaderText(headerText) {
    const text = (headerText || '').trim();
    const textLower = text.toLowerCase();
    if (text.includes('备注')) return 'remark';
    if (text.includes('澳洲小包限定标准')) return 'standard';
    if (text.includes('挂号费') || text.includes('单价')) return 'fee';
    if (text.includes('首') || text.includes('续') || text.includes('重量') || textLower.includes('kg')) return 'price';
    return 'generic';
}

function getDynamicColumnRole(headerCell, colIndex, totalColumns) {
    if (!headerCell || !isDynamicWeightColumn(colIndex, totalColumns)) return null;
    const roleFromData = (headerCell.dataset && headerCell.dataset.colRole ? headerCell.dataset.colRole : '').trim().toLowerCase();
    if (roleFromData) return roleFromData;
    if (headerCell.classList.contains('col-remark')) return 'remark';
    if (headerCell.classList.contains('col-standard')) return 'standard';
    if (headerCell.classList.contains('col-fee')) return 'fee';
    if (headerCell.classList.contains('col-price')) return 'price';
    return detectColumnRoleByHeaderText(headerCell.textContent || '');
}

function collectColumnGroups(headerCells) {
    const groups = {
        remarkColIndices: [],
        standardColIndices: [],
        priceColIndices: [],
        feePriceColIndices: []
    };
    const totalColumns = headerCells.length;
    Array.from(headerCells).forEach((headerCell, colIndex) => {
        const role = getDynamicColumnRole(headerCell, colIndex, totalColumns);
        if (!role) return;
        if (role === 'remark') groups.remarkColIndices.push(colIndex);
        else if (role === 'standard') groups.standardColIndices.push(colIndex);
        else if (role === 'price') groups.priceColIndices.push(colIndex);
        else if (role === 'fee') groups.feePriceColIndices.push(colIndex);
    });
    return groups;
}

function normalizeDynamicColumnClasses(table, headerCells) {
    const tbody = table.tBodies[0];
    const totalColumns = headerCells.length;
    Array.from(headerCells).forEach((headerCell, colIndex) => {
        const role = getDynamicColumnRole(headerCell, colIndex, totalColumns);
        if (!role) return;
        headerCell.dataset.colRole = role;
        headerCell.classList.add('col-dynamic', 'col-' + role);
        if (role === 'remark') headerCell.classList.add('remark-header');
        if (role === 'standard') headerCell.classList.add('standard-header');
        if (!tbody) return;
        Array.from(tbody.rows).forEach(row => {
            const cell = row.cells[colIndex];
            if (!cell) return;
            cell.dataset.colRole = role;
            cell.classList.add('col-dynamic', 'col-' + role);
            if (role === 'remark') cell.classList.add('remark-cell');
            if (role === 'standard') cell.classList.add('standard-cell');
        });
    });
}

function setupChannelNavigator() {
    const panel = document.getElementById('channelNavPanel');
    const list = document.getElementById('channelNavList');
    const searchWrapEl = document.getElementById('cnpSearchWrap');
    const searchCodeEl = document.getElementById('cnpSearchCode');
    const searchClearBtn = document.getElementById('cnpSearchClearBtn');
    if (!panel || !list) return;

    const syncNavSearchCode = function() {
        if (!searchWrapEl && !searchCodeEl && !searchClearBtn) return;
        const mainInput = document.getElementById('globalPostcodeInput');
        const queryText = (mainInput && mainInput.value ? mainInput.value.trim() : '');
        const hasQuery = /^\d{4}$/.test(queryText);
        const shouldShow = hasQuery && panel.hasAttribute('open');
        if (searchWrapEl) {
            searchWrapEl.hidden = !shouldShow;
        }
        if (searchCodeEl && shouldShow) {
            searchCodeEl.textContent = queryText;
        } else if (searchCodeEl) {
            searchCodeEl.textContent = '';
        }
        if (searchClearBtn) {
            searchClearBtn.hidden = !shouldShow;
        }
    };
    if (searchClearBtn) {
        // 防止在 <summary> 内点击按钮时触发展开/收起
        const stopSummaryToggle = function(e) {
            e.preventDefault();
            e.stopPropagation();
        };
        searchClearBtn.addEventListener('mousedown', stopSummaryToggle);
        searchClearBtn.addEventListener('click', function(e) {
            stopSummaryToggle(e);
            const mainInput = document.getElementById('globalPostcodeInput');
            if (mainInput) {
                mainInput.value = '';
            }
            if (typeof window.globalSearchPostcode === 'function') {
                window.globalSearchPostcode();
            }
            if (window.matchMedia('(max-width: 768px)').matches) {
                panel.removeAttribute('open');
            }
            syncNavSearchCode();
        });
    }
    panel.addEventListener('toggle', syncNavSearchCode);

    const modules = Array.from(document.querySelectorAll('.module-channel'));
    list.innerHTML = '';
    if (modules.length === 0) {
        panel.hidden = true;
        return;
    }

    // 每个 module 对应一个 li，存到 map 便于后续同步
    const moduleNavMap = new Map();

    modules.forEach((module, idx) => {
        if (!module.id) module.id = 'channel-module-' + (idx + 1);
        const titleEl = module.querySelector('.channel-title');
        const titleText = (titleEl && titleEl.textContent ? titleEl.textContent.trim() : '') || ('渠道 ' + (idx + 1));

        const li = document.createElement('li');
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'channel-nav-link';
        btn.textContent = titleText;
        btn.setAttribute('aria-label', '定位到' + titleText);
        btn.addEventListener('click', function() {
            const sessionId = ++CHANNEL_NAV_SCROLL_SESSION;
            list.querySelectorAll('.channel-nav-link.active').forEach(function(node) {
                node.classList.remove('active');
            });
            btn.classList.add('active');
            const titleTarget = module.querySelector('.channel-title') || module;
            const tableEl = module.querySelector('.channel-table');
            const getTargetTop = function() {
                // 目录跳转优先对齐到表头，确保移动端能同时看到渠道标题和价格表标题行
                const tableHeadEl = tableEl && tableEl.tHead ? tableEl.tHead : null;
                const anchorEl = tableHeadEl || tableEl || titleTarget || module;
                const anchorAbsoluteTop = anchorEl.getBoundingClientRect().top + window.pageYOffset;
                const navHeight = getCurrentNavHeight();
                const titleHeight = titleTarget ? titleTarget.offsetHeight : 0;
                const visualOffset = navHeight + titleHeight + SCROLL_NUDGE_PX;
                return Math.max(0, anchorAbsoluteTop - visualOffset);
            };
            const convergeToTarget = function(behavior) {
                window.scrollTo({ top: getTargetTop(), behavior: behavior || 'auto' });
            };
            const runFinalSettle = function(onDone) {
                if (sessionId !== CHANNEL_NAV_SCROLL_SESSION) return;
                convergeToTarget('auto');
                requestAnimationFrame(function() {
                    requestAnimationFrame(function() {
                        if (sessionId !== CHANNEL_NAV_SCROLL_SESSION) return;
                        const finalTargetTop = getTargetTop();
                        const delta = finalTargetTop - window.scrollY;
                        if (Math.abs(delta) > FINAL_SETTLE_EPS_PX) {
                            window.scrollBy({ top: delta, behavior: 'auto' });
                        }
                        // sticky 场景下部分浏览器会在下一帧继续修正布局，再补一次收敛
                        requestAnimationFrame(function() {
                            if (sessionId !== CHANNEL_NAV_SCROLL_SESSION) return;
                            const finalTargetTop2 = getTargetTop();
                            const delta2 = finalTargetTop2 - window.scrollY;
                            if (Math.abs(delta2) > FINAL_SETTLE_EPS_PX) {
                                window.scrollBy({ top: delta2, behavior: 'auto' });
                            }
                            if (typeof onDone === 'function') onDone();
                        });
                    });
                });
            };
            let released = false;
            let onScrollEnd = null;
            const releaseAutoScroll = function() {
                if (released) return;
                released = true;
                if (sessionId !== CHANNEL_NAV_SCROLL_SESSION) {
                    if (onScrollEnd) {
                        window.removeEventListener('scrollend', onScrollEnd);
                        onScrollEnd = null;
                    }
                    return;
                }
                clearChannelNavAutoScrollTimers();
                if (onScrollEnd) {
                    window.removeEventListener('scrollend', onScrollEnd);
                    if (CHANNEL_NAV_ACTIVE_SCROLLEND_HANDLER === onScrollEnd) {
                        CHANNEL_NAV_ACTIVE_SCROLLEND_HANDLER = null;
                    }
                    onScrollEnd = null;
                }
                // 先做最终收敛与偏差修正，之后再恢复 sticky，避免旧模块 sticky 抢占
                runFinalSettle(function() {
                    const releaseTimerId = window.setTimeout(function() {
                        if (sessionId !== CHANNEL_NAV_SCROLL_SESSION) return;
                        IS_CHANNEL_NAV_AUTO_SCROLLING = false;
                        STICKY_SUPPRESS_UNTIL = Date.now() + STICKY_SUPPRESS_AFTER_NAV_MS;
                        LAST_STICKY_REFRESH();
                    }, STICKY_RESUME_DELAY_MS);
                    CHANNEL_NAV_AUTO_SCROLL_TIMERS.push(releaseTimerId);
                });
            };

            clearChannelNavAutoScrollTimers();
            if (CHANNEL_NAV_ACTIVE_SCROLLEND_HANDLER) {
                window.removeEventListener('scrollend', CHANNEL_NAV_ACTIVE_SCROLLEND_HANDLER);
                CHANNEL_NAV_ACTIVE_SCROLLEND_HANDLER = null;
            }
            IS_CHANNEL_NAV_AUTO_SCROLLING = true;
            STICKY_SUPPRESS_UNTIL = Date.now() + AUTO_SCROLL_FALLBACK_MS + STICKY_SUPPRESS_AFTER_NAV_MS;
            document.querySelectorAll('.sticky-header-container.active').forEach(function(container) {
                container.classList.remove('active');
            });

            // 先平滑滚动到目标，再进行阶段性收敛；结束时做最终收敛
            convergeToTarget('smooth');
            [180, 420, 760, 980].forEach(function(delay) {
                const timerId = window.setTimeout(function() {
                    if (sessionId !== CHANNEL_NAV_SCROLL_SESSION) return;
                    convergeToTarget('auto');
                }, delay);
                CHANNEL_NAV_AUTO_SCROLL_TIMERS.push(timerId);
            });
            if ('onscrollend' in window) {
                onScrollEnd = function() {
                    window.removeEventListener('scrollend', onScrollEnd);
                    if (CHANNEL_NAV_ACTIVE_SCROLLEND_HANDLER === onScrollEnd) {
                        CHANNEL_NAV_ACTIVE_SCROLLEND_HANDLER = null;
                    }
                    onScrollEnd = null;
                    releaseAutoScroll();
                };
                CHANNEL_NAV_ACTIVE_SCROLLEND_HANDLER = onScrollEnd;
                window.addEventListener('scrollend', onScrollEnd);
            }
            CHANNEL_NAV_AUTO_SCROLL_TIMERS.push(window.setTimeout(releaseAutoScroll, AUTO_SCROLL_FALLBACK_MS));

            if (window.matchMedia('(max-width: 768px)').matches) {
                panel.removeAttribute('open');
            }
        });
        li.appendChild(btn);
        list.appendChild(li);
        moduleNavMap.set(module, li);
    });

    // 暴露同步函数：根据 module 上的 hidden-by-search 状态同步目录项的显示
    window._syncChannelNavWithSearch = function() {
        let visibleCount = 0;
        const mainInput = document.getElementById('globalPostcodeInput');
        const queryText = (mainInput && mainInput.value ? mainInput.value.trim() : '');
        const hasQuery = /^\d{4}$/.test(queryText);
        syncNavSearchCode();
        moduleNavMap.forEach(function(li, module) {
            const hasVisibleRows = !!module.querySelector('.channel-table tbody tr:not(.hidden-by-search)');
            const isHiddenByFlag = module.classList.contains('hidden-by-search');
            // 兜底：搜索态下按“是否有可见数据行”同步目录，避免仅依赖 hidden-by-search 标记失效
            const isHidden = isHiddenByFlag || (hasQuery && !hasVisibleRows);
            li.classList.toggle('hidden-in-nav', isHidden);
            li.querySelector('.channel-nav-link').classList.toggle('hidden-in-nav', isHidden);
            if (isHidden) {
                li.querySelector('.channel-nav-link').classList.remove('active');
            }
            if (!isHidden) visibleCount++;
        });
        // 搜索有结果时自动展开目录；清空搜索时显示全部
        if (visibleCount > 0 && visibleCount < modules.length) {
            panel.setAttribute('open', '');
        }
    };
    syncNavSearchCode();
}


function setupPostcodePopover() {
    const popover = document.getElementById('postcodePopover');
    const postcodeCells = Array.from(document.querySelectorAll('.channel-table td.postcode-cell'));
    if (postcodeCells.length === 0) {
        if (popover) popover.hidden = true;
        return;
    }

    if (popover) {
        popover.hidden = true;
        popover.textContent = '';
    }
    document.querySelectorAll('.postcode-cell-active').forEach(function(cell) {
        cell.classList.remove('postcode-cell-active');
    });

    const blockEvent = function(e) {
        e.preventDefault();
        e.stopPropagation();
    };

    postcodeCells.forEach(function(cell) {
        cell.tabIndex = -1;
        cell.removeAttribute('role');
        cell.setAttribute('aria-label', '邮编');
        cell.style.userSelect = 'none';
        cell.style.webkitUserSelect = 'none';
        cell.style.cursor = 'default';
        cell.setAttribute('draggable', 'false');

        ['click', 'dblclick', 'contextmenu', 'copy', 'cut', 'dragstart', 'selectstart'].forEach(function(type) {
            cell.addEventListener(type, blockEvent);
        });
    });

    const isNodeInsidePostcodeCell = function(node) {
        let current = node;
        while (current) {
            if (current.nodeType === 1 && current.matches && current.matches('.channel-table td.postcode-cell')) {
                return true;
            }
            current = current.parentNode;
        }
        return false;
    };

    document.addEventListener('copy', function(e) {
        const selection = window.getSelection ? window.getSelection() : null;
        if (!selection || selection.rangeCount === 0) return;
        if (isNodeInsidePostcodeCell(selection.anchorNode) || isNodeInsidePostcodeCell(selection.focusNode)) {
            e.preventDefault();
        }
    }, true);
}

function setupBackToTop() {
    var btn = document.getElementById('backToTop');
    if (!btn) return;
    function toggleVisible() {
        btn.style.visibility = (window.pageYOffset > 300) ? 'visible' : 'hidden';
        btn.style.opacity = (window.pageYOffset > 300) ? '1' : '0';
    }
    toggleVisible();
    window.addEventListener('scroll', toggleVisible, { passive: true });
    btn.addEventListener('click', function() {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
}

// 文章详情内图片：点击全屏，支持双指缩放与拖动
function setupImageLightbox() {
    const lightbox = document.getElementById('imageLightbox');
    const lightboxImg = document.getElementById('imageLightboxImg');
    const lightboxWrap = lightbox && lightbox.querySelector('.image-lightbox-img-wrap');
    const closeBtn = document.getElementById('imageLightboxClose');
    if (!lightbox || !lightboxImg || !lightboxWrap) return;

    const MIN_SCALE = 1;
    const MAX_SCALE = 5;
    let scale = 1, translateX = 0, translateY = 0;
    let lastDist = 0;
    let startSingleX = 0, startSingleY = 0, startTx = 0, startTy = 0;
    let rafId = null;

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function clampTranslation(nextScale, nextTranslateX, nextTranslateY) {
        const contentWidth = lightboxWrap.offsetWidth * nextScale;
        const contentHeight = lightboxWrap.offsetHeight * nextScale;
        const maxOffsetX = Math.max(0, (contentWidth - window.innerWidth) / 2);
        const maxOffsetY = Math.max(0, (contentHeight - window.innerHeight) / 2);
        return {
            x: clamp(nextTranslateX, -maxOffsetX, maxOffsetX),
            y: clamp(nextTranslateY, -maxOffsetY, maxOffsetY)
        };
    }

    function applyTransform() {
        if (rafId !== null) return;
        rafId = requestAnimationFrame(function() {
            lightboxWrap.style.transform = 'translate(' + translateX + 'px,' + translateY + 'px) scale(' + scale + ')';
            rafId = null;
        });
    }

    function resetTransform() {
        scale = MIN_SCALE;
        translateX = 0;
        translateY = 0;
        lastDist = 0;
        if (rafId !== null) cancelAnimationFrame(rafId);
        rafId = null;
        lightboxWrap.style.transform = 'translate(0,0) scale(' + MIN_SCALE + ')';
    }

    function openLightbox(src, alt) {
        lightboxImg.src = src;
        lightboxImg.alt = alt || '';
        resetTransform();
        lightbox.classList.add('active');
        lightbox.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden';
    }

    function closeLightbox() {
        lightbox.classList.remove('active');
        lightbox.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
    }

    document.querySelectorAll('.module-image img').forEach(function(img) {
        img.addEventListener('click', function(e) {
            e.preventDefault();
            openLightbox(img.src, img.alt);
        });
    });

    closeBtn.addEventListener('click', closeLightbox);
    lightbox.addEventListener('click', function(e) {
        if (e.target === lightbox) closeLightbox();
    });

    lightboxWrap.addEventListener('touchstart', function(e) {
        if (e.touches.length === 2) {
            const d = Math.hypot(e.touches[1].clientX - e.touches[0].clientX, e.touches[1].clientY - e.touches[0].clientY);
            lastDist = d;
        } else if (e.touches.length === 1) {
            startSingleX = e.touches[0].clientX;
            startSingleY = e.touches[0].clientY;
            startTx = translateX;
            startTy = translateY;
        }
    }, { passive: true });

    lightboxWrap.addEventListener('touchmove', function(e) {
        if (e.touches.length === 2) {
            e.preventDefault();
            const d = Math.hypot(e.touches[1].clientX - e.touches[0].clientX, e.touches[1].clientY - e.touches[0].clientY);
            if (lastDist <= 0) lastDist = d;
            const cx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
            const cy = (e.touches[0].clientY + e.touches[1].clientY) / 2;
            const scaleFactor = d / lastDist;
            lastDist = d;
            const currentRect = lightboxWrap.getBoundingClientRect();
            const currentScale = scale || MIN_SCALE;
            const pointXInImage = (cx - currentRect.left) / currentScale;
            const pointYInImage = (cy - currentRect.top) / currentScale;
            const baseLeft = currentRect.left - translateX;
            const baseTop = currentRect.top - translateY;
            const newScale = clamp(scale * scaleFactor, MIN_SCALE, MAX_SCALE);
            const nextTranslateX = cx - baseLeft - pointXInImage * newScale;
            const nextTranslateY = cy - baseTop - pointYInImage * newScale;
            const clamped = clampTranslation(newScale, nextTranslateX, nextTranslateY);
            translateX = clamped.x;
            translateY = clamped.y;
            scale = newScale;
            applyTransform();
        } else if (e.touches.length === 1) {
            e.preventDefault();
            if (scale <= MIN_SCALE) return;
            const nextTranslateX = startTx + (e.touches[0].clientX - startSingleX);
            const nextTranslateY = startTy + (e.touches[0].clientY - startSingleY);
            const clamped = clampTranslation(scale, nextTranslateX, nextTranslateY);
            translateX = clamped.x;
            translateY = clamped.y;
            applyTransform();
        }
    }, { passive: false });

    /* 双指变单指时更新单指起点，避免松指瞬间位移跳动 */
    lightboxWrap.addEventListener('touchend', function(e) {
        if (e.touches.length === 1) {
            startSingleX = e.touches[0].clientX;
            startSingleY = e.touches[0].clientY;
            startTx = translateX;
            startTy = translateY;
        } else if (e.touches.length === 0) {
            lastDist = 0;
        }
        if (scale <= MIN_SCALE) {
            resetTransform();
            return;
        }
        const clamped = clampTranslation(scale, translateX, translateY);
        translateX = clamped.x;
        translateY = clamped.y;
        applyTransform();
    }, { passive: true });
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && lightbox.classList.contains('active')) closeLightbox();
    });
}

// 高亮渠道标题中横杠前面的文本
function highlightChannelNames() {
    const channelTitles = document.querySelectorAll('.channel-title');
    channelTitles.forEach(title => {
        const text = title.textContent;
        // 查找横杠（可能是"一"、"—"或"-"）
        const dashMatch = text.match(/^(.+?)([一—\-])(.+)$/);
        if (dashMatch) {
            const beforeDash = dashMatch[1]; // 横杠前面的文本
            const dash = dashMatch[2]; // 横杠本身
            const afterDash = dashMatch[3]; // 横杠后面的文本
            
            // 用span包裹横杠前面的文本，添加高亮样式
            title.innerHTML = `<span class="channel-name-highlight">${beforeDash}</span>${dash}${afterDash}`;
        }
    });
}

function destroyStickyTableHeaders() {
    if (!STICKY_RUNTIME) return;
    STICKY_RUNTIME.globalListeners.forEach(function(item) {
        item.target.removeEventListener(item.type, item.handler, item.options);
    });
    STICKY_RUNTIME.moduleRecords.forEach(function(record) {
        if (record.wrapper && record.onWrapperScroll) {
            record.wrapper.removeEventListener('scroll', record.onWrapperScroll);
        }
        if (record.container && record.onContainerScroll) {
            record.container.removeEventListener('scroll', record.onContainerScroll);
        }
        if (record.onWindowScrollSyncWidth) {
            window.removeEventListener('scroll', record.onWindowScrollSyncWidth);
        }
        if (record.moduleObserver) record.moduleObserver.disconnect();
        if (record.tableHeadObserver) record.tableHeadObserver.disconnect();
        if (record.container) record.container.remove();
    });
    STICKY_RUNTIME = null;
}

// 动态设置表头的置顶位置，使其紧贴在渠道标题下方
function setupStickyTableHeaders() {
    destroyStickyTableHeaders();
    if (!shouldUseClonedStickyHeaders()) {
        LAST_STICKY_REFRESH = function() {};
        return;
    }

    const channelModules = Array.from(document.querySelectorAll('.module-channel'));
    const runtime = {
        moduleRecords: [],
        globalListeners: [],
        refreshQueued: false
    };

    function updateAllStickyHeaders() {
        const navHeight = getCurrentNavHeight();
        const allStickyContainers = runtime.moduleRecords.map(function(record) {
            return record.container;
        });
        if (IS_CHANNEL_NAV_AUTO_SCROLLING || Date.now() < STICKY_SUPPRESS_UNTIL) {
            allStickyContainers.forEach(function(container) {
                container.classList.remove('active');
            });
            return;
        }

        const anchorTop = navHeight + 1;
        let bestPassedRecord = null;
        let bestPassedTitleOffset = -Infinity;
        let bestUpcomingRecord = null;
        let bestUpcomingTitleOffset = Infinity;

        for (let i = 0; i < runtime.moduleRecords.length; i++) {
            const record = runtime.moduleRecords[i];
            const module = record.module;
            const title = record.channelTitle;
            const table = record.table;
            const wrapper = record.wrapper;
            if (!module || !title || !table || !table.tHead || !wrapper) continue;

            const moduleRect = module.getBoundingClientRect();
            const titleRect = title.getBoundingClientRect();
            const wrapperRect = wrapper.getBoundingClientRect();
            const stickyTop = navHeight + title.offsetHeight;
            const isModuleInView = moduleRect.bottom > navHeight && moduleRect.top < window.innerHeight;
            const isTitleVisibleForSticky = titleRect.bottom > navHeight;
            const isWithinTableRegion = wrapperRect.top <= stickyTop && wrapperRect.bottom > stickyTop;
            if (!isModuleInView || !isTitleVisibleForSticky || !isWithinTableRegion) continue;

            const titleOffset = titleRect.top - anchorTop;
            if (titleOffset <= 0) {
                // 优先选择“标题刚经过导航栏锚点”的渠道，避免上行时命中过早渠道
                if (titleOffset > bestPassedTitleOffset) {
                    bestPassedTitleOffset = titleOffset;
                    bestPassedRecord = record;
                }
            } else if (titleOffset < bestUpcomingTitleOffset) {
                bestUpcomingTitleOffset = titleOffset;
                bestUpcomingRecord = record;
            }
        }
        const topRecordToShow = bestPassedRecord || bestUpcomingRecord || null;

        runtime.moduleRecords.forEach(function(record) {
            const container = record.container;
            if (record === topRecordToShow) {
                if (record.updateWidth) record.updateWidth();
                if (record.wrapper) container.scrollLeft = record.wrapper.scrollLeft;
                container.classList.add('active');
            } else {
                container.classList.remove('active');
            }
        });
    }

    function requestStickyRefresh() {
        if (runtime.refreshQueued) return;
        runtime.refreshQueued = true;
        requestAnimationFrame(function() {
            runtime.refreshQueued = false;
            updateAllStickyHeaders();
        });
    }

    LAST_STICKY_REFRESH = requestStickyRefresh;

    channelModules.forEach(function(module) {
        const channelTitle = module.querySelector('.channel-title');
        const table = module.querySelector('.channel-table');
        const wrapper = module.querySelector('.channel-table-wrapper');
        if (!channelTitle || !table || !table.tHead || !wrapper) return;

        const stickyContainer = document.createElement('div');
        stickyContainer.className = 'sticky-header-container';
        stickyContainer.style.top = (getCurrentNavHeight() + channelTitle.offsetHeight) + 'px';

        function updateStickyContainerWidth() {
            stickyContainer.style.top = (getCurrentNavHeight() + channelTitle.offsetHeight) + 'px';
            const currentTitleRect = channelTitle.getBoundingClientRect();
            stickyContainer.style.width = currentTitleRect.width + 'px';
            stickyContainer.style.left = currentTitleRect.left + 'px';
            stickyContainer.style.transform = 'none';
            if (stickyContainer._syncColumnWidths) stickyContainer._syncColumnWidths();
        }
        updateStickyContainerWidth();

        const clonedTable = table.cloneNode(true);
        clonedTable.querySelector('tbody')?.remove();
        const originalHeaderCells = table.tHead.rows[0].cells;
        const clonedHeaderCells = clonedTable.tHead.rows[0].cells;
        clonedTable.style.width = table.offsetWidth + 'px';
        clonedTable.style.minWidth = table.offsetWidth + 'px';
        for (let i = 0; i < originalHeaderCells.length; i++) {
            const originalWidth = originalHeaderCells[i].offsetWidth;
            if (clonedHeaderCells[i]) {
                clonedHeaderCells[i].style.width = originalWidth + 'px';
                clonedHeaderCells[i].style.minWidth = originalWidth + 'px';
            }
        }
        clonedTable.style.tableLayout = 'fixed';
        stickyContainer.appendChild(clonedTable);
        document.body.appendChild(stickyContainer);

        const syncColumnWidths = function() {
            for (let i = 0; i < originalHeaderCells.length && i < clonedHeaderCells.length; i++) {
                const originalWidth = originalHeaderCells[i].offsetWidth;
                clonedHeaderCells[i].style.width = originalWidth + 'px';
                clonedHeaderCells[i].style.minWidth = originalWidth + 'px';
            }
            const currentTableWidth = table.offsetWidth;
            clonedTable.style.width = currentTableWidth + 'px';
            clonedTable.style.minWidth = currentTableWidth + 'px';
        };
        stickyContainer._syncColumnWidths = syncColumnWidths;

        const onWindowScrollSyncWidth = function() {
            if (stickyContainer.classList.contains('active')) {
                requestAnimationFrame(updateStickyContainerWidth);
            }
        };
        window.addEventListener('scroll', onWindowScrollSyncWidth, { passive: true });

        const onWrapperScroll = function() {
            if (stickyContainer.classList.contains('active')) {
                stickyContainer.scrollLeft = wrapper.scrollLeft;
            }
        };
        wrapper.addEventListener('scroll', onWrapperScroll, { passive: true });

        const onContainerScroll = function() {
            if (stickyContainer.classList.contains('active')) {
                wrapper.scrollLeft = stickyContainer.scrollLeft;
            }
        };
        stickyContainer.addEventListener('scroll', onContainerScroll, { passive: true });

        const moduleObserver = new IntersectionObserver(function() {
            requestStickyRefresh();
        }, {
            root: null,
            rootMargin: `-${getCurrentNavHeight()}px 0px -50% 0px`,
            threshold: [0, 0.1, 0.5]
        });
        moduleObserver.observe(module);

        const tableHeadObserver = new IntersectionObserver(function() {
            requestStickyRefresh();
        }, {
            root: null,
            rootMargin: `-${(getCurrentNavHeight() + channelTitle.offsetHeight)}px 0px 0px 0px`,
            threshold: 0
        });
        tableHeadObserver.observe(table.tHead);

        runtime.moduleRecords.push({
            module: module,
            channelTitle: channelTitle,
            table: table,
            wrapper: wrapper,
            container: stickyContainer,
            updateWidth: updateStickyContainerWidth,
            onWindowScrollSyncWidth: onWindowScrollSyncWidth,
            onWrapperScroll: onWrapperScroll,
            onContainerScroll: onContainerScroll,
            moduleObserver: moduleObserver,
            tableHeadObserver: tableHeadObserver
        });
    });

    const onScrollGlobal = requestStickyRefresh;
    const onTouchMoveGlobal = requestStickyRefresh;
    const onTouchEndGlobal = requestStickyRefresh;
    const onTouchEndDelayedGlobal = function() {
        setTimeout(requestStickyRefresh, 150);
    };

    window.addEventListener('scroll', onScrollGlobal, { passive: true });
    document.addEventListener('touchmove', onTouchMoveGlobal, { passive: true });
    document.addEventListener('touchend', onTouchEndGlobal, { passive: true });
    document.addEventListener('touchend', onTouchEndDelayedGlobal, { passive: true });

    runtime.globalListeners.push(
        { target: window, type: 'scroll', handler: onScrollGlobal, options: { passive: true } },
        { target: document, type: 'touchmove', handler: onTouchMoveGlobal, options: { passive: true } },
        { target: document, type: 'touchend', handler: onTouchEndGlobal, options: { passive: true } },
        { target: document, type: 'touchend', handler: onTouchEndDelayedGlobal, options: { passive: true } }
    );

    STICKY_RUNTIME = runtime;
    requestStickyRefresh();
}

// 窗口调整大小时重新计算
let resizeTimer;
window.addEventListener('resize', function() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function() {
        syncNavHeightVar();
        setupStickyTableHeaders();
    }, 250);
});

// 设置表格行的hover效果，使合并单元格也显示hover
function setupTableRowHover() {
    const tables = document.querySelectorAll('.channel-table');
    tables.forEach(table => {
        const tbody = table.tBodies[0];
        if (!tbody) return;
        
        const allRows = Array.from(tbody.rows);
        
        // 获取表头来确定列索引
        const headerCells = table.tHead && table.tHead.rows[0] ? table.tHead.rows[0].cells : null;
        if (!headerCells) return;
        
        const stateColIndex = 0;  // 目的港列索引
        const postcodeColIndex = 1;  // 邮编列索引
        const areaColIndex = 2;  // 区域列索引
        const deliveryColIndex = headerCells.length - 2;  // 到门时效列索引
        const channelColIndex = headerCells.length - 1;  // 渠道列索引
        
        const columnGroups = collectColumnGroups(headerCells);
        const remarkColIndices = columnGroups.remarkColIndices;
        const standardColIndices = columnGroups.standardColIndices;
        const priceColIndices = columnGroups.priceColIndices;
        const feePriceColIndices = columnGroups.feePriceColIndices;
        
        allRows.forEach(row => {
            row.addEventListener('mouseenter', function() {
                const rowIndex = allRows.indexOf(this);
                if (rowIndex === -1) return;
                
                // 给当前行添加hover类
                this.classList.add('row-hover');
                
                // 找到目的港合并单元格，并给合并单元格添加hover类
                const mergedStateCell = findMergedCellOwner(allRows, rowIndex, stateColIndex);
                if (mergedStateCell && mergedStateCell.rowSpan > 1) {
                    mergedStateCell.classList.add('merged-cell-hover');
                }
                
                // 找到邮编合并单元格，并给合并单元格添加hover类
                const mergedPostcodeCell = findMergedCellOwner(allRows, rowIndex, postcodeColIndex);
                if (mergedPostcodeCell && mergedPostcodeCell.rowSpan > 1) {
                    mergedPostcodeCell.classList.add('merged-cell-hover');
                }
                
                // 找到区域合并单元格，并给合并单元格添加hover类
                const mergedAreaCell = findMergedCellOwner(allRows, rowIndex, areaColIndex);
                if (mergedAreaCell && mergedAreaCell.rowSpan > 1) {
                    mergedAreaCell.classList.add('merged-cell-hover');
                }
                
                // 找到到门时效合并单元格，并给合并单元格添加hover类
                const mergedDeliveryCell = findMergedCellOwner(allRows, rowIndex, deliveryColIndex);
                if (mergedDeliveryCell && mergedDeliveryCell.rowSpan > 1) {
                    mergedDeliveryCell.classList.add('merged-cell-hover');
                }
                
                // 找到渠道合并单元格，并给合并单元格添加hover类
                const mergedChannelCell = findMergedCellOwner(allRows, rowIndex, channelColIndex);
                if (mergedChannelCell && mergedChannelCell.rowSpan > 1) {
                    mergedChannelCell.classList.add('merged-cell-hover');
                }
                
                // 找到所有备注列的合并单元格，并给合并单元格添加hover类
                remarkColIndices.forEach(remarkColIndex => {
                    const mergedRemarkCell = findMergedCellOwner(allRows, rowIndex, remarkColIndex);
                    if (mergedRemarkCell && mergedRemarkCell.rowSpan > 1) {
                        mergedRemarkCell.classList.add('merged-cell-hover');
                    }
                });
                
                // 找到所有"澳洲小包限定标准"列的合并单元格，并给合并单元格添加hover类
                standardColIndices.forEach(standardColIndex => {
                    const mergedStandardCell = findMergedCellOwner(allRows, rowIndex, standardColIndex);
                    if (mergedStandardCell && mergedStandardCell.rowSpan > 1) {
                        mergedStandardCell.classList.add('merged-cell-hover');
                    }
                });
                
                // 找到所有首重和续重列的合并单元格，并给合并单元格添加hover类
                priceColIndices.forEach(priceColIndex => {
                    const mergedPriceCell = findMergedCellOwner(allRows, rowIndex, priceColIndex);
                    if (mergedPriceCell && mergedPriceCell.rowSpan > 1) {
                        mergedPriceCell.classList.add('merged-cell-hover');
                    }
                });
                
                // 找到所有"挂号费"和"单价"列的合并单元格，并给合并单元格添加hover类
                feePriceColIndices.forEach(feePriceColIndex => {
                    const mergedFeePriceCell = findMergedCellOwner(allRows, rowIndex, feePriceColIndex);
                    if (mergedFeePriceCell && mergedFeePriceCell.rowSpan > 1) {
                        mergedFeePriceCell.classList.add('merged-cell-hover');
                    }
                });
            });
            
            row.addEventListener('mouseleave', function() {
                // 移除当前行的hover类
                this.classList.remove('row-hover');
                
                // 移除所有合并单元格的hover类
                const tbody = this.closest('tbody');
                if (tbody) {
                    tbody.querySelectorAll('.merged-cell-hover').forEach(cell => {
                        cell.classList.remove('merged-cell-hover');
                    });
                }
            });
        });
    });
}

/** 取消表格合并，恢复所有单元格（rowSpan=1、display 恢复），搜索前/清空搜索后调用 */
function unmergeAllChannelTables() {
    document.querySelectorAll('.channel-table').forEach(table => {
        unmergeSingleChannelTable(table);
    });
}

function unmergeSingleChannelTable(table) {
    const tbody = table && table.tBodies ? table.tBodies[0] : null;
    if (!tbody) return;
    Array.from(tbody.rows).forEach(row => {
        Array.from(row.cells).forEach(cell => {
            if (cell.rowSpan > 1) cell.rowSpan = 1;
        });
    });
    tbody.querySelectorAll('td').forEach(td => {
        if (td.style.display === 'none') td.style.display = '';
    });
}

function mergeSingleChannelTable(table) {
    const headerCells = table.tHead && table.tHead.rows[0] ? table.tHead.rows[0].cells : null;
    if (!headerCells) return;

    const colIndexState = 0;                 // 目的港
    const colIndexPostcode = 1;              // 邮编
    const colIndexArea = 2;                  // 区域
    const colIndexDelivery = headerCells.length - 2; // 到门时效
    const colIndexChannel = headerCells.length - 1;  // 渠道

    // 合并相同目的港
    mergeTableColumn(table, colIndexState, 'mergeState');
    // 合并相同邮编
    mergeTableColumn(table, colIndexPostcode, 'mergePostcode');
    // 合并相同区域（包含空值或“-”等显示值）
    mergeTableColumn(table, colIndexArea, 'mergeArea');
    // 合并相同到门时效（只在相同目的港的情况下合并）
    mergeTableColumnWithState(table, colIndexDelivery, 'mergeDelivery', colIndexState);
    // 合并相同渠道
    mergeTableColumn(table, colIndexChannel, 'mergeChannel');

    // 统一按语义 role 标准化 class，并执行动态列合并
    normalizeDynamicColumnClasses(table, headerCells);
    const columnGroups = collectColumnGroups(headerCells);
    columnGroups.remarkColIndices.forEach(colIndex => mergeRemarkColumn(table, colIndex));
    columnGroups.standardColIndices.forEach(colIndex => mergeRemarkColumn(table, colIndex));
    columnGroups.priceColIndices.forEach(colIndex => mergeRemarkColumn(table, colIndex));
    columnGroups.feePriceColIndices.forEach(colIndex => mergeRemarkColumn(table, colIndex));
}

function mergeAllChannelTables() {
    const tables = document.querySelectorAll('.channel-table');
    tables.forEach(table => {
        mergeSingleChannelTable(table);
    });
}

function mergeTableColumn(table, colIndex, dataKey) {
    const tbody = table.tBodies[0];
    if (!tbody) return;

    let lastCell = null;
    let spanCount = 1;

    Array.from(tbody.rows).forEach(row => {
        if (row.classList.contains('hidden-by-search')) {
            lastCell = null;
            spanCount = 1;
            return;
        }
        const cell = row.cells[colIndex];
        if (!cell) return;

        const hasKeyAttr = !!(dataKey && Object.prototype.hasOwnProperty.call(cell.dataset, dataKey));
        const keyAttr = hasKeyAttr ? cell.dataset[dataKey] : null;
        const text = hasKeyAttr ? String(keyAttr).trim() : cell.textContent.trim();

        const lastHasKeyAttr = !!(dataKey && Object.prototype.hasOwnProperty.call(lastCell ? lastCell.dataset : {}, dataKey));
        const lastText = lastHasKeyAttr ? String(lastCell.dataset[dataKey]).trim() : (lastCell ? lastCell.textContent.trim() : '');

        if (lastCell && text === lastText) {
            spanCount += 1;
            lastCell.rowSpan = spanCount;
            cell.style.display = 'none';
        } else {
            lastCell = cell;
            spanCount = 1;
        }
    });
}

// 合并到门时效列，但只在相同目的港的情况下合并
function mergeTableColumnWithState(table, colIndex, dataKey, stateColIndex) {
    const tbody = table.tBodies[0];
    if (!tbody) return;

    let lastCell = null;
    let lastStateValue = null;
    let spanCount = 1;
    const allRows = Array.from(tbody.rows);

    Array.from(tbody.rows).forEach((row, rowIndex) => {
        if (row.classList.contains('hidden-by-search')) {
            lastCell = null;
            lastStateValue = null;
            spanCount = 1;
            return;
        }
        const cell = row.cells[colIndex];
        let stateCell = row.cells[stateColIndex];
        if (!cell) return;

        // 如果目的港单元格被隐藏，向上查找可见的目的港单元格
        if (stateCell && stateCell.style.display === 'none') {
            for (let i = rowIndex - 1; i >= 0; i--) {
                const prevStateCell = allRows[i].cells[stateColIndex];
                if (prevStateCell && prevStateCell.style.display !== 'none') {
                    stateCell = prevStateCell;
                    break;
                }
            }
        }

        if (!stateCell) return;

        const keyAttr = dataKey ? cell.dataset[dataKey] : null;
        const text = keyAttr ? keyAttr : cell.textContent.trim();
        const stateValue = stateCell.dataset.mergeState || stateCell.textContent.trim();

        // 只有当目的港相同且到门时效也相同时才合并
        if (lastCell && lastStateValue === stateValue && text && text === (dataKey ? lastCell.dataset[dataKey] : lastCell.textContent.trim())) {
            spanCount += 1;
            lastCell.rowSpan = spanCount;
            cell.style.display = 'none';
        } else {
            lastCell = cell;
            lastStateValue = stateValue;
            spanCount = 1;
        }
    });
}

// 合并备注列：相邻行相同内容即合并，不考虑目的港
function mergeRemarkColumn(table, colIndex) {
    const tbody = table.tBodies[0];
    if (!tbody) return;

    let lastCell = null;
    let spanCount = 1;

    Array.from(tbody.rows).forEach(row => {
        if (row.classList.contains('hidden-by-search')) {
            lastCell = null;
            spanCount = 1;
            return;
        }
        const cell = row.cells[colIndex];
        if (!cell) return;

        const text = cell.textContent.trim();

        // 只要相邻行的内容相同就合并，不考虑目的港
        if (lastCell && text && text === lastCell.textContent.trim()) {
            spanCount += 1;
            lastCell.rowSpan = spanCount;
            cell.style.display = 'none';
        } else {
            lastCell = cell;
            spanCount = 1;
        }
    });
}

// 全局邮编查询（同一邮编同时查大件/纸箱；根据渠道状态组合显示文案）
async function globalSearchPostcode() {
    if (GLOBAL_SEARCH_TIMER) {
        clearTimeout(GLOBAL_SEARCH_TIMER);
        GLOBAL_SEARCH_TIMER = null;
    }
    const mainInput = document.getElementById('globalPostcodeInput');
    const main = (mainInput && mainInput.value.trim()) || '';
    var postcode = main.length === 4 && /^\d{4}$/.test(main) ? main : '';
    const resultSpan = document.getElementById('globalSearchResult');
    if (!resultSpan) return;

    document.querySelectorAll('.channel-table tbody tr').forEach(tr => {
        tr.classList.remove('highlight');
        tr.classList.remove('hidden-by-search');
    });
    document.querySelectorAll('.channel-table tbody td.merged-cell-highlight').forEach(td => {
        td.classList.remove('merged-cell-highlight');
    });
    document.querySelectorAll('.module-channel').forEach(module => {
        module.classList.remove('hidden-by-search');
        module.classList.remove('search-mode');
    });

    if (postcode.length === 0) {
        unmergeAllChannelTables();
        mergeAllChannelTables();
        resultSpan.textContent = '';
        resultSpan.className = 'search-result';
        if (typeof window._syncChannelNavWithSearch === 'function') window._syncChannelNavWithSearch();
        if (typeof LAST_STICKY_REFRESH === 'function') LAST_STICKY_REFRESH();
        return;
    }

    // 生成本次查询的 token，防止上一次请求晚返回覆盖结果
    const queryToken = ++GLOBAL_POSTCODE_QUERY_TOKEN;

    resultSpan.textContent = '查询中...';

    // 按邮编匹配渠道表：匹配才高亮，不匹配不高亮（兼容所有渠道类型）
    function renderMatchedRowsForCode(code) {
        unmergeAllChannelTables();
        document.querySelectorAll('.module-channel').forEach(module => {
            const tableWrapper = module.querySelector('.channel-table-wrapper');
            if (!tableWrapper) {
                module.classList.add('hidden-by-search');
                return;
            }
            const matchedRows = [];
            tableWrapper.querySelectorAll('tbody tr').forEach(tr => {
                const range = tr.dataset.postcodeRange;
                if (range && isPostcodeInRange(code, range)) {
                    matchedRows.push(tr);
                } else {
                    tr.classList.add('hidden-by-search');
                }
            });
            if (matchedRows.length === 0) {
                module.classList.add('hidden-by-search');
            } else {
                module.classList.add('search-mode');
                highlightMatchedRowsWithMergedCells(matchedRows);
            }
        });
        // 同步渠道目录：只显示搜索匹配的渠道
        if (typeof window._syncChannelNavWithSearch === 'function') window._syncChannelNavWithSearch();
        mergeAllChannelTables();
        requestAnimationFrame(function() {
            document.querySelectorAll('.module-channel:not(.hidden-by-search) .channel-table-wrapper').forEach(function(w) {
                w.style.width = '100%';
                w.style.maxWidth = '100%';
            });
            if (typeof LAST_STICKY_REFRESH === 'function') LAST_STICKY_REFRESH();
        });
    }

    try {
        const evaluate = await fetchPostcodeEvaluate(
            postcode,
            function() { return queryToken !== GLOBAL_POSTCODE_QUERY_TOKEN; }
        );
        if (evaluate.aborted || queryToken !== GLOBAL_POSTCODE_QUERY_TOKEN) {
            return;
        }
        const evaluateData = evaluate.data;
        if (!evaluateData || !evaluateData.data) {
            resultSpan.textContent = getPostcodeEvaluateMessage(evaluateData, evaluate.error);
            resultSpan.className = 'search-result error';
            renderMatchedRowsForCode(postcode);
            return;
        }
        const payload = evaluateData.data || {};
        const prefix = buildChannelServicePrefix(payload.dajian_status, payload.zhixiang_status);
        const hasDistance = hasValidDistance(payload.distance);
        const unitText = normalizeDistanceUnit(payload.distance_unit);
        const distanceText = hasDistance ? ('距离：' + payload.distance + unitText) : '';

        if (!hasDistance) {
            resultSpan.textContent = getPostcodeEvaluateMessage(evaluateData, evaluate.error);
            resultSpan.className = 'search-result error';
            renderMatchedRowsForCode(postcode);
            return;
        }

        resultSpan.textContent = prefix && distanceText ? (prefix + ';' + distanceText) : (prefix || distanceText);
        resultSpan.className = 'search-result success';
        renderMatchedRowsForCode(postcode);
    } catch (error) {
        resultSpan.textContent = error && error.name === 'AbortError' ? '网络不佳，请稍后重试' : '查询服务暂时不可用，请稍后再试';
        resultSpan.className = 'search-result error';
    }
}

// 单个模块邮编查询
async function autoSearchPostcode(input) {
    const postcode = input.value.trim();
    
    if (postcode.length !== 4 || !/^\d{4}$/.test(postcode)) {
        return;
    }
    
    const wrapper = input.closest('.module-channel');
    const tableWrapper = wrapper.querySelector('.channel-table-wrapper');
    const tableEl = tableWrapper ? tableWrapper.querySelector('.channel-table') : null;
    const resultSpan = wrapper.querySelector('.distance-result');
    
    resultSpan.textContent = '查询中...';
    
    try {
        const evaluate = await fetchPostcodeEvaluate(postcode);
        const data = evaluate.data;
        if (!data || !data.data) {
            resultSpan.textContent = getPostcodeEvaluateMessage(data, evaluate.error);
            return;
        }
        const payload = data.data || {};
        const prefix = buildChannelServicePrefix(payload.dajian_status, payload.zhixiang_status);
        const hasDistance = hasValidDistance(payload.distance);
        if (!hasDistance) {
            resultSpan.textContent = getPostcodeEvaluateMessage(data, evaluate.error);
            return;
        }
        const unitText = normalizeDistanceUnit(payload.distance_unit);
        resultSpan.textContent = prefix ? `${prefix}距离：${payload.distance}${unitText}` : `距离：${payload.distance}${unitText}`;

        // 先在当前模块内重建合并态，避免合并 owner 失真导致“合并格不上色”
        if (tableEl) {
            unmergeSingleChannelTable(tableEl);
            mergeSingleChannelTable(tableEl);
        }

        tableWrapper.querySelectorAll('tr').forEach(tr => tr.classList.remove('highlight'));
        tableWrapper.querySelectorAll('td.merged-cell-highlight').forEach(td => td.classList.remove('merged-cell-highlight'));
        
        const matchedRows = [];
        tableWrapper.querySelectorAll('tbody tr').forEach(tr => {
            const range = tr.dataset.postcodeRange;
            if (range && isPostcodeInRange(postcode, range)) {
                matchedRows.push(tr);
            }
        });
        // 高亮所有匹配的行及其合并单元格
        highlightMatchedRowsWithMergedCells(matchedRows);
    } catch (error) {
        resultSpan.textContent = '服务暂不可用，请稍后重试';
    }
}

// 高亮所有匹配的行及其合并单元格（只高亮邮编匹配的行）
function highlightMatchedRowsWithMergedCells(matchedRows) {
    if (!matchedRows || matchedRows.length === 0) return;
    
    const tbody = matchedRows[0].closest('tbody');
    if (!tbody) return;
    
    const allRows = Array.from(tbody.rows);
    
    // 获取表头来确定列索引
    const headerCells = tbody.closest('table').tHead && tbody.closest('table').tHead.rows[0] ? tbody.closest('table').tHead.rows[0].cells : null;
    if (!headerCells) return;
    
    const stateColIndex = 0;  // 目的港列索引
    const postcodeColIndex = 1;  // 邮编列索引
    const areaColIndex = 2;  // 区域列索引
    const deliveryColIndex = headerCells.length - 2;  // 到门时效列索引
    const channelColIndex = headerCells.length - 1;  // 渠道列索引
    
    const columnGroups = collectColumnGroups(headerCells);
    const remarkColIndices = columnGroups.remarkColIndices;
    const standardColIndices = columnGroups.standardColIndices;
    const priceColIndices = columnGroups.priceColIndices;
    const feePriceColIndices = columnGroups.feePriceColIndices;
    
    // 只高亮匹配的行
    matchedRows.forEach(targetRow => {
        targetRow.classList.add('highlight');
        
        const targetIndex = allRows.indexOf(targetRow);
        if (targetIndex === -1) return;
        
        // 找到目的港合并单元格，并给合并单元格添加高亮类
        const mergedStateCell = findMergedCellOwner(allRows, targetIndex, stateColIndex);
        if (mergedStateCell) {
            mergedStateCell.classList.add('merged-cell-highlight');
        }
        
        // 找到邮编合并单元格，并给合并单元格添加高亮类
        const mergedPostcodeCell = findMergedCellOwner(allRows, targetIndex, postcodeColIndex);
        if (mergedPostcodeCell) {
            mergedPostcodeCell.classList.add('merged-cell-highlight');
        }
        
        // 找到区域合并单元格，并给合并单元格添加高亮类
        const mergedAreaCell = findMergedCellOwner(allRows, targetIndex, areaColIndex);
        if (mergedAreaCell) {
            mergedAreaCell.classList.add('merged-cell-highlight');
        }
        
        // 找到到门时效合并单元格，并给合并单元格添加高亮类
        const mergedDeliveryCell = findMergedCellOwner(allRows, targetIndex, deliveryColIndex);
        if (mergedDeliveryCell) {
            mergedDeliveryCell.classList.add('merged-cell-highlight');
        }
        
        // 找到渠道合并单元格，并给合并单元格添加高亮类
        const mergedChannelCell = findMergedCellOwner(allRows, targetIndex, channelColIndex);
        if (mergedChannelCell) {
            mergedChannelCell.classList.add('merged-cell-highlight');
        }
        
        // 找到所有备注列的合并单元格，并给合并单元格添加高亮类
        remarkColIndices.forEach(remarkColIndex => {
            const mergedRemarkCell = findMergedCellOwner(allRows, targetIndex, remarkColIndex);
            if (mergedRemarkCell) {
                mergedRemarkCell.classList.add('merged-cell-highlight');
            }
        });
        
        // 找到所有"澳洲小包限定标准"列的合并单元格，并给合并单元格添加高亮类
        standardColIndices.forEach(standardColIndex => {
            const mergedStandardCell = findMergedCellOwner(allRows, targetIndex, standardColIndex);
            if (mergedStandardCell) {
                mergedStandardCell.classList.add('merged-cell-highlight');
            }
        });
        
        // 找到所有首重和续重列的合并单元格，并给合并单元格添加高亮类
        priceColIndices.forEach(priceColIndex => {
            const mergedPriceCell = findMergedCellOwner(allRows, targetIndex, priceColIndex);
            if (mergedPriceCell) {
                mergedPriceCell.classList.add('merged-cell-highlight');
            }
        });
        
        // 找到所有"挂号费"和"单价"列的合并单元格，并给合并单元格添加高亮类
        feePriceColIndices.forEach(feePriceColIndex => {
            const mergedFeePriceCell = findMergedCellOwner(allRows, targetIndex, feePriceColIndex);
            if (mergedFeePriceCell) {
                mergedFeePriceCell.classList.add('merged-cell-highlight');
            }
        });
    });
}

// 找到指定行和列的合并单元格所有者
function findMergedCellOwner(allRows, rowIndex, colIndex) {
    if (rowIndex < 0 || rowIndex >= allRows.length || colIndex < 0) return null;
    
    const currentCell = allRows[rowIndex].cells[colIndex];
    if (!currentCell) return null;
    
    // 如果当前单元格有rowSpan，它就是所有者
    if (currentCell.rowSpan > 1) {
        return currentCell;
    }
    
    // 如果当前单元格被隐藏，向上查找所有者
    if (currentCell.style.display === 'none') {
        for (let i = rowIndex - 1; i >= 0; i--) {
            const cell = allRows[i].cells[colIndex];
            if (!cell) break;
            
            // 如果找到有rowSpan的单元格，检查是否包含当前行
            if (cell.rowSpan > 1) {
                const spanEnd = i + cell.rowSpan;
                if (rowIndex < spanEnd) {
                    return cell;
                }
            }
            
            // 如果遇到可见的单元格，说明不在这个合并范围内
            if (cell.style.display !== 'none') {
                break;
            }
        }
    }
    
    return currentCell;
}

function isPostcodeInRange(postcode, range) {
    if (!range) return false;

    // 统一成 4 位字符串，不做任何推断，只按源数据精确/区间匹配
    const code = String(postcode).trim();
    if (!/^\d{4}$/.test(code)) return false;

    range = String(range).trim();

    // 1）范围格式：2000~2999 或 2000-2999
    const rangeMatch = range.match(/^\s*(\d{4})\s*[~-]\s*(\d{4})\s*$/);
    if (rangeMatch) {
        const start = parseInt(rangeMatch[1], 10);
        const end = parseInt(rangeMatch[2], 10);
        const num = parseInt(code, 10);
        return num >= start && num <= end;
    }

    // 2）逗号分隔列表：完全按源数据里的每一段对比，不因为 ... 推断中间值
    if (range.includes(',')) {
        const parts = range.split(',').map(p => p.trim()).filter(p => p);
        return parts.some(p => p === code);
    }

    // 3）单个邮编
    if (/^\d{4}$/.test(range)) {
        return range === code;
    }

    return false;
}
