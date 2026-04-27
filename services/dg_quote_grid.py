# -*- coding: utf-8 -*-
"""DG 报价表模块：新模块默认用 empty_dg_grid_content() 空白网格外加；仍保留从 xlsx 读表的工具函数。"""
from datetime import date, datetime
import html
import os

import config

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None  # type: ignore

# 提单号表头行：与 xlsx 中「提单号」行一致，用于全行橙色底
_TIDAN_MARKER = '提单号'
# 仅当「最宽行」列数仍像原始全表（≥13）时才裁，避免对已存好的 9 列数据重复裁切
DG_EDGE_TRIM_MIN_COLS = 13
_DROP_HEAD_COLS = 1
_DROP_TAIL_COLS = 3
_COL_KEEP_LO = 2
_COL_KEEP_HI = 10  # 保留 B–J 共 9 列（1-based）


def _abs_path():
    p = config.DG_QUOTE_XLSX_PATH
    return p if os.path.isabs(p) else os.path.join(config._BASE_DIR, p)


def _cell_to_str(value):
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M')[: 16] if value.hour or value.minute else value.strftime('%Y-%m-%d')
    if isinstance(value, date):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value)
    return str(value)


def _read_sheet_grid_with_sources(ws):
    max_row, max_col = (ws.max_row or 0), (ws.max_column or 0)
    if max_row < 1 or max_col < 1:
        return 'Sheet1', 1, 1, [['']], [1]

    cells = []
    row_sources = []
    for r in range(1, max_row + 1):
        line = []
        for c in range(1, max_col + 1):
            line.append(_cell_to_str(ws.cell(r, c).value))
        cells.append(line)
        row_sources.append(r)
    return (ws.title or 'Sheet1'), max_row, max_col, cells, row_sources


def _row_is_empty(row):
    if not row:
        return True
    for c in row:
        s = (str(c).strip() if c is not None else '')
        if s:
            return False
    return True


def trim_leading_empty_rows(cells):
    """去掉顶部连续全空行（xlsx 常有一行空表头行）。"""
    if not cells:
        return cells
    out = list(cells)
    while out and _row_is_empty(out[0]):
        out.pop(0)
    return out if out else [['']]


def trim_leading_empty_rows_paired(cells, row_sources):
    """与 trim_leading_empty_rows 同步裁剪 row_sources（1-based Excel 行号）。"""
    if not cells or not row_sources or len(cells) != len(row_sources):
        return cells, row_sources
    out = list(cells)
    out_s = list(row_sources)
    while out and _row_is_empty(out[0]):
        out.pop(0)
        out_s.pop(0)
    if not out:
        return [['']], (out_s[:1] if out_s else [1])
    return out, out_s


def trim_dg_edge_columns(cells):
    """去掉每行左侧第 1 列与右侧最后 3 列（宽表用）。"""
    if not cells:
        return cells
    max_w = max((len(r) for r in cells if r is not None), default=0)
    if max_w < DG_EDGE_TRIM_MIN_COLS:
        return list(cells)
    need = _DROP_HEAD_COLS + _DROP_TAIL_COLS
    out = []
    for row in cells:
        if not row:
            out.append([])
            continue
        r = list(row)
        n = len(r)
        if n <= need:
            out.append([])
        else:
            out.append(r[_DROP_HEAD_COLS : n - _DROP_TAIL_COLS])
    return out if out else [['']]


def normalize_dg_grid_cells(cells):
    """展示/保存/导入用：去顶空行 + 去左 1 列与右 3 列。"""
    if not cells:
        return [['']]
    cells = trim_leading_empty_rows(cells)
    cells = trim_dg_edge_columns(cells)
    return cells if cells else [['']]


def find_tidan_header_row(cells):
    """返回含「提单号」的行的 0-based 索引；找不到返回 None。"""
    if not cells:
        return None
    for i, row in enumerate(cells):
        for c in row or []:
            if _TIDAN_MARKER in str(c or ''):
                return i
    return None


def _clip_excel_merge_to_output(mr, row_sources, c_lo, c_hi):
    """将 xlsx 合并区映射到去列后的网格（0-based，含 rs/cs）。"""
    mmin_r, mmin_c, mmax_r, mmax_c = mr.min_row, mr.min_col, mr.max_row, mr.max_col
    rows_in = [i for i, er in enumerate(row_sources) if mmin_r <= er <= mmax_r]
    if not rows_in:
        return None
    cc1 = max(mmin_c, c_lo)
    cc2 = min(mmax_c, c_hi)
    if cc1 > cc2:
        return None
    o_r1, o_r2 = min(rows_in), max(rows_in)
    o_c1, o_c2 = cc1 - 2, cc2 - 2
    rs, cs = o_r2 - o_r1 + 1, o_c2 - o_c1 + 1
    return {'r': o_r1, 'c': o_c1, 'rs': rs, 'cs': cs}


def _collect_output_merges(ws, row_sources, c_lo=_COL_KEEP_LO, c_hi=_COL_KEEP_HI):
    if not row_sources or not hasattr(ws, 'merged_cells') or not ws.merged_cells:
        return []
    out = []
    for mr in ws.merged_cells.ranges:
        item = _clip_excel_merge_to_output(mr, row_sources, c_lo, c_hi)
        if item and item['rs'] >= 1 and item['cs'] >= 1:
            out.append(item)
    return out


def _pad_cells(cells, n, m):
    if not cells:
        return [[''] * m for _ in range(n)]
    out = []
    for i in range(n):
        row = list(cells[i]) if i < len(cells) and cells[i] is not None else []
        if len(row) < m:
            row = row + [''] * (m - len(row))
        else:
            row = row[:m]
        out.append(row)
    return out


def prepare_dg_table_display(c):
    """
    与文章页 / dg_grid_render 过滤器前处理一致。
    入参 c 为模块 content 字典。返回 (cells, merges, header_orange_row)（尚未 pad/推断 merges 前的 merges 可仍为 None/[]）。
    """
    if c is None or not isinstance(c, dict):
        return ([[""]], None, None)
    cells = c.get("cells") or []
    merges = c.get("merges")
    if isinstance(merges, list) and len(merges) > 0:
        cells = [list(x) for x in cells]
    else:
        cells = normalize_dg_grid_cells(cells)
    hr = c.get("header_orange_row")
    if hr is not None and hr != "":
        try:
            hr = int(hr)
        except (TypeError, ValueError):
            hr = find_tidan_header_row(cells)
    else:
        hr = find_tidan_header_row(cells)
    return (cells, merges, hr)


def _prepare_dg_table_structure(cells, merges, header_orange_row):
    """
    Pad、推断全宽合并、建立 omit 矩阵。cells 为二维 list。
    返回 (cells, merges, n, m, omit, header_orange_row)（merges 为生效后的 list）。
    """
    cells = [list(r) for r in (cells or [])] if cells else [[]]
    n = len(cells)
    m = max((len(r) for r in cells if r), default=0) if n else 0
    if n < 1:
        m = max(m, 1)
        n = 1
    m = max(m, 1)
    cells = _pad_cells(cells, n, m)

    merges = list(merges or [])
    if not merges:
        merges = infer_full_width_row_merges(cells)

    omit = [[False] * m for _ in range(n)]
    for mg in merges:
        r, c, rs, cs = int(mg["r"]), int(mg["c"]), int(mg["rs"]), int(mg["cs"])
        if r < 0 or c < 0 or r + rs > n or c + cs > m:
            continue
        for i in range(r, r + rs):
            for j in range(c, c + cs):
                if i < n and j < m:
                    omit[i][j] = True
        omit[r][c] = "anchor"

    if header_orange_row is None:
        header_orange_row = find_tidan_header_row(cells)
    return (cells, merges, n, m, omit, header_orange_row)


def infer_full_width_row_merges(cells):
    """
    无 merges 元数据时（旧数据），对前两行按「整行仅第 1 格有字、其余为空」推断为 B:J 式整行合并
    （与 xlsx 中「中海豹国际」「报价单」两行一致）；其余行不推断，避免误判。
    """
    if not cells:
        return []
    m = max((len(r) for r in cells if r), default=0)
    if m <= 1:
        return []
    out = []
    for r in range(min(2, len(cells))):
        row = cells[r]
        rline = list(row) + [''] * (m - len(row))
        if not str(rline[0] or '').strip():
            continue
        if not all(not str(rline[i] or '').strip() for i in range(1, m)):
            continue
        out.append({'r': r, 'c': 0, 'rs': 1, 'cs': m})
    return out


def render_dg_table_html(cells, merges=None, header_orange_row=None):
    """
    渲染与 xlsx 布局一致的 <table>（含 rowspan/colspan）；
    header_orange_row 为 0-based 行号时该行使用橙色表头底（与提单号行一致）。
    """
    raw = [list(r) for r in (cells or [])] if cells else [[]]
    cells, merges, n, m, omit, header_orange_row = _prepare_dg_table_structure(
        raw, merges, header_orange_row
    )

    parts = ['<table class="dg-grid-table" role="table"><tbody>']
    for r in range(n):
        tr_cls = ' dg-grid-tr-tidan-header' if header_orange_row is not None and r == header_orange_row else ''
        parts.append(f'<tr class="dg-grid-tr{tr_cls}">')
        for c in range(m):
            op = omit[r][c]
            if op is True:
                continue
            val = cells[r][c] if c < len(cells[r]) else ''
            text = html.escape(str(val) if val is not None else '', quote=True)
            if op == 'anchor':
                mg = next((g for g in merges if g['r'] == r and g['c'] == c), None)
                rs, cs2 = 1, 1
                if mg:
                    rs, cs2 = int(mg['rs']), int(mg['cs'])
                # 整行合并（如「中海豹国际」B2:J2）需取消 max-width，否则看起来像未合并
                full = ' dg-grid-cell--fullrow' if cs2 >= m else ''
                attr = f' class="dg-grid-cell{full}" rowspan="{rs}" colspan="{cs2}"'
            else:
                attr = ' class="dg-grid-cell"'
            parts.append(f'<td{attr}>{text}</td>')
        parts.append('</tr>')
    parts.append('</tbody></table>')
    return ''.join(parts)


def render_dg_table_editable_html(cells, merges=None, header_orange_row=None):
    """
    与 render_dg_table_html 同布局，单元格为 <textarea> 供后台编辑（锚点格带 data-r/data-c）。
    """
    raw = [list(r) for r in (cells or [])] if cells else [[]]
    cells, merges, n, m, omit, header_orange_row = _prepare_dg_table_structure(
        raw, merges, header_orange_row
    )

    parts = ['<table class="dg-grid-table dg-grid-table--editable" role="table"><tbody>']
    for r in range(n):
        tr_cls = ' dg-grid-tr-tidan-header' if header_orange_row is not None and r == header_orange_row else ''
        parts.append(f'<tr class="dg-grid-tr{tr_cls}">')
        for c in range(m):
            op = omit[r][c]
            if op is True:
                continue
            val = cells[r][c] if c < len(cells[r]) else ''
            text = html.escape(str(val) if val is not None else '', quote=False)
            if op == 'anchor':
                mg = next((g for g in merges if g['r'] == r and g['c'] == c), None)
                rs, cs2 = 1, 1
                if mg:
                    rs, cs2 = int(mg['rs']), int(mg['cs'])
                full = ' dg-grid-cell--fullrow' if cs2 >= m else ''
                attr = f' class="dg-grid-cell{full}" rowspan="{rs}" colspan="{cs2}"'
            else:
                attr = ' class="dg-grid-cell"'
            parts.append(
                f'<td{attr}><textarea class="dg-grid-cell-input" data-r="{r}" data-c="{c}" '
                f'rows="1" spellcheck="false" autocomplete="off">{text}</textarea></td>'
            )
        parts.append(
            '<td class="dg-grid-cell dg-grid-cell--row-action" data-dg-side="actions">'
            '<button type="button" class="dg-row-del-btn" data-dg-act="remove-row" title="删除本行">'
            "删除</button></td>"
        )
        parts.append('</tr>')
    parts.append('</tbody></table>')
    return ''.join(parts)


# --- DG 模块 schema v2：对齐「4月25日DG周鹏.xlsx」结构（上：客户与单证，下：费用表）---
DG_SCHEMA_V2 = 2


def default_dg_v2_customer_rows():
    """与项目根目录「4月25日DG周鹏.xlsx」Sheet1 客户区一致（5 行 × 两列键值对）。"""
    return [
        {"label": "客户ID编码：", "value": "周鹏"},
        {"label": "报价日期：", "value": "2026-04-25"},
        {"label": "客户地址：", "value": ""},
        {
            "label": "公司地址：",
            "value": "深圳市宝安区福永街道翠岗西路怀德国际大厦1801",
        },
        {"label": "联系人/职位：", "value": ""},
        {"label": "联系人：", "value": ""},
        {"label": "电话/分机：", "value": ""},
        {"label": "电话：", "value": ""},
        {"label": "Email：", "value": ""},
        {"label": "Email：", "value": ""},
    ]


def default_dg_v2_logistics():
    """起运/目的/船名；提单号与柜号在费用表各列，不在此表。与 4月25日DG周鹏.xlsx 起运/单证区一致。"""
    return {
        "origin_port": "深圳",
        "transit_port": "",
        "dest_port": "SYD海外仓",
        "dest_warehouse": "",
        "vessel": "",
    }


def _dg_v2_fee_subtotal(name, amount, note=""):
    """与 xlsx 一致：「费用项目」+「币别」合并为同一格（row_kind=subtotal）。"""
    amt = ""
    if amount is not None and amount != "":
        amt = str(amount)
    return {
        "row_kind": "subtotal",
        "bl_no": "",
        "container_no": "",
        "name": name,
        "currency": "",
        "amount": amt,
        "note": note or "",
    }


def default_dg_v2_fee_items():
    """与 4月25日DG周鹏.xlsx 费用行一致（含 RMB 小计、汇率说明行、总合计，合并格同表）。"""
    z = {"row_kind": "item", "bl_no": "", "container_no": ""}
    return [
        {
            "name": "Container loading fee/装箱费",
            "currency": "RMB",
            "amount": "800",
            "note": "",
            **z,
        },
        {
            "name": "customs clearance fee/报关费",
            "currency": "RMB",
            "amount": "500",
            "note": "",
            **z,
        },
        {
            "name": "Maritime declaration fee/海事申报费",
            "currency": "RMB",
            "amount": "不确定项",
            "note": "400/个un",
            **z,
        },
        {
            "name": "supervision fee for loading/危险品监装费",
            "currency": "RMB",
            "amount": "不确定项",
            "note": "低消1500，超出550/小时",
            **z,
        },
        {
            "name": "container stuffing charge/提箱费",
            "currency": "RMB",
            "amount": "2000",
            "note": "",
            **z,
        },
        _dg_v2_fee_subtotal("RMB合计", 3300),
        {
            "name": "B/L surrender fee/电放费",
            "currency": "RMB",
            "amount": "450",
            "note": "",
            **z,
        },
        {
            "name": "Sealing fee/封条费",
            "currency": "RMB",
            "amount": "50",
            "note": "",
            **z,
        },
        {
            "name": "THC/码头操作费",
            "currency": "RMB",
            "amount": "900",
            "note": "",
            **z,
        },
        {
            "name": "DOC/文件",
            "currency": "RMB",
            "amount": "450",
            "note": "",
            **z,
        },
        {
            "name": "ocean freight/海运费",
            "currency": "USD",
            "amount": "1750",
            "note": "20GP",
            **z,
        },
        _dg_v2_fee_subtotal("RMB合计", 14450),
        {
            "name": "码头杂费",
            "currency": "AUD",
            "amount": "850",
            "note": "",
            **z,
        },
        {
            "name": "DOC/文件费",
            "currency": "AUD",
            "amount": "300",
            "note": "",
            **z,
        },
        {
            "name": "Custom Clearance清关费+税金",
            "currency": "AUD",
            "amount": "180",
            "note": "税金实报实销",
            **z,
        },
        {
            "name": "Devanning/拆柜",
            "currency": "AUD",
            "amount": "",
            "note": "",
            **z,
        },
        {
            "name": "Delivery fee/派送费",
            "currency": "AUD",
            "amount": "1100",
            "note": "",
            **z,
        },
        _dg_v2_fee_subtotal("RMB合计", 11907),
        _dg_v2_fee_subtotal("美金/澳币兑换人民币汇率", "", "参考即时汇率"),
        _dg_v2_fee_subtotal("总合计", 29657, "不包含不确定项"),
    ]


def is_dg_content_v2(c):
    """新结构：schema=2。旧数据无此字段，仍为合并单元格矩阵。"""
    if not c or not isinstance(c, dict):
        return False
    try:
        return int(c.get("schema") or 0) == DG_SCHEMA_V2
    except (TypeError, ValueError):
        return False


def merge_dg_v2_content(d):
    """补全 v2 缺省键，供后台/渲染使用。"""
    d = dict(d) if d else {}
    if not d.get("customer_rows") or not isinstance(d.get("customer_rows"), list):
        d["customer_rows"] = default_dg_v2_customer_rows()
    else:
        # 与默认条数不一致时只保留已有，不强制补齐
        pass
    if not d.get("logistics") or not isinstance(d.get("logistics"), dict):
        d["logistics"] = default_dg_v2_logistics()
    else:
        base = default_dg_v2_logistics()
        base.update(d["logistics"])
        d["logistics"] = base
    # 旧数据：提单/柜在 logistics 时迁入费用行
    mbl = (d.get("logistics") or {}).get("bl_no") or ""
    mct = (d.get("logistics") or {}).get("container_no") or ""
    if "logistics" in d and isinstance(d["logistics"], dict):
        d["logistics"].pop("bl_no", None)
        d["logistics"].pop("container_no", None)
    if not d.get("fee_items") or not isinstance(d.get("fee_items"), list):
        d["fee_items"] = default_dg_v2_fee_items()
    elif len(d["fee_items"]) == 0:
        d["fee_items"] = default_dg_v2_fee_items()
    any_bl = any(
        (isinstance(x, dict) and (x.get("bl_no") or "").strip()) for x in (d.get("fee_items") or [])
    )
    any_cn = any(
        (isinstance(x, dict) and (x.get("container_no") or "").strip()) for x in (d.get("fee_items") or [])
    )
    for it in d.get("fee_items") or []:
        if not isinstance(it, dict):
            continue
        it.setdefault("bl_no", "")
        it.setdefault("container_no", "")
        rk = it.get("row_kind") or "item"
        it["row_kind"] = rk if rk in ("item", "subtotal") else "item"
        if it["row_kind"] == "subtotal":
            it["currency"] = ""
        if it["row_kind"] != "subtotal":
            if mbl and not any_bl and not (it.get("bl_no") or "").strip():
                it["bl_no"] = mbl
            if mct and not any_cn and not (it.get("container_no") or "").strip():
                it["container_no"] = mct
    d.setdefault("header_main", "中海豹国际")
    d.setdefault("header_sub", "报价单")
    d.pop("footer_note", None)
    d.setdefault("title", "DG 报价表")
    d.setdefault("template_file", "")
    d.setdefault("sheet_name", "")
    d.setdefault("remark", "")
    d["schema"] = DG_SCHEMA_V2
    return d


def render_dg_quote_v2_html(c):
    """文章页 v2 模块 HTML（抬头+客户+单证与 Excel 同一张四列表，不含模块标题与 remark 外层）。"""
    from markupsafe import Markup, escape

    c = merge_dg_v2_content(c if isinstance(c, dict) else {})
    parts = ['<div class="dg-quote-v2">']
    # 与 xlsx 一致：大标题、副标题、客户两列一组、起运/单证两列一组，合并在同一 <table> 内
    parts.append(
        '<table class="dg-quote-v2-meta" role="presentation" aria-label="报价抬头与客户及单证">'
    )
    parts.append(
        f'<tr class="dg-c-main"><th colspan="4" scope="col">{escape(c.get("header_main") or "")}</th></tr>'
    )
    parts.append(
        f'<tr class="dg-c-sub"><th colspan="4" scope="col">{escape(c.get("header_sub") or "")}</th></tr>'
    )
    cust = c.get("customer_rows") or []
    for i in range(0, len(cust), 2):
        a, b = cust[i], (cust[i + 1] if i + 1 < len(cust) else None)
        parts.append(
            f'<tr class="dg-c-kv">'
            f'<th scope="row" class="dg-c-lab">{escape(str(a.get("label") or ""))}</th>'
            f'<td class="dg-c-val">{escape(str(a.get("value") or ""))}</td>'
        )
        if b is not None:
            parts.append(
                f'<th scope="row" class="dg-c-lab">{escape(str(b.get("label") or ""))}</th>'
                f'<td class="dg-c-val">{escape(str(b.get("value") or ""))}</td></tr>'
            )
        else:
            parts.append('<th scope="row" class="dg-c-lab"></th><td class="dg-c-val"></td></tr>')
    log = c.get("logistics") or {}
    log_quads = [
        (
            ("起运港", "origin_port"),
            ("中转港", "transit_port"),
        ),
        (
            ("目的港", "dest_port"),
            ("海外仓", "dest_warehouse"),
        ),
    ]
    for quad in log_quads:
        left, right = quad[0], quad[1]
        parts.append(
            f'<tr class="dg-c-log">'
            f'<th scope="row" class="dg-c-lab">{escape(left[0])}</th>'
            f'<td class="dg-c-val">{escape(str(log.get(left[1]) or ""))}</td>'
            f'<th scope="row" class="dg-c-lab">{escape(right[0])}</th>'
            f'<td class="dg-c-val">{escape(str(log.get(right[1]) or ""))}</td></tr>'
        )
    # 提单号、柜号在下方费用表各列
    parts.append(
        f'<tr class="dg-c-log">'
        f'<th scope="row" class="dg-c-lab">船名航次</th>'
        f'<td class="dg-c-val">{escape(str(log.get("vessel") or ""))}</td>'
        f'<th scope="row" class="dg-c-lab"></th><td class="dg-c-val"></td></tr>'
    )
    parts.append("</table>")
    parts.append(
        '<div class="dg-quote-v2-section dg-quote-v2-fee-block">'
        '<h3 class="dg-quote-v2-sec-title">费用项目</h3>'
        '<div class="dg-quote-v2-fee-wrap"><table class="dg-quote-v2-fee">'
        "<thead><tr><th>提单号</th><th>柜号</th><th>费用项目</th><th>币别</th><th>金额</th><th>说明</th></tr></thead><tbody>"
    )
    for it in c.get("fee_items") or []:
        is_sub = (it.get("row_kind") or "item") == "subtotal"
        cl = "dg-quote-v2-fee-tr" + (
            " dg-quote-v2-fee-tr--subtotal" if is_sub else " dg-quote-v2-fee-tr--item"
        )
        parts.append(f'<tr class="{cl}">')
        if is_sub:
            parts.append(f"<td>{escape(str(it.get('bl_no') or ''))}</td>")
            parts.append(f"<td>{escape(str(it.get('container_no') or ''))}</td>")
            parts.append(
                f'<td colspan="2" class="dg-c-fee-merged-lab">'
                f"{escape(str(it.get('name') or ''))}</td>"
            )
            parts.append(f"<td>{escape(str(it.get('amount') or ''))}</td>")
            parts.append(f"<td>{escape(str(it.get('note') or ''))}</td>")
        else:
            for k in ("bl_no", "container_no", "name", "currency", "amount", "note"):
                parts.append(f"<td>{escape(str(it.get(k) or ''))}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table></div></div>")
    parts.append("</div>")
    return Markup("".join(parts))


def empty_dg_grid_content():
    """
    新建「DG 报价表」：schema v2，结构对齐 4月25日DG周鹏.xlsx（客户信息 + 费用表，可增删行）。
    """
    return {
        "schema": DG_SCHEMA_V2,
        "title": "DG 报价表",
        "header_main": "中海豹国际",
        "header_sub": "报价单",
        "customer_rows": default_dg_v2_customer_rows(),
        "logistics": default_dg_v2_logistics(),
        "fee_items": default_dg_v2_fee_items(),
        "template_file": "",
        "sheet_name": "",
        "remark": (
            "PS:如果遇到海关查验，查验费用实报实销。\n\n"
            "备注：2620"
        ),
    }


def build_default_dg_grid_content():
    """读取默认 xlsx 第一页，包含合并与提单号行号（与源表一致，需非 read_only 以读 merged_cells）。"""
    if load_workbook is None:
        return _fallback_with_meta('未安装 openpyxl，请执行 pip install -r requirements.txt')

    path = _abs_path()
    if not os.path.isfile(path):
        return _fallback_with_meta(
            f'未找到文件：{os.path.basename(path)}，请将文件放在项目根目录或设置环境变量 DG_QUOTE_XLSX_PATH'
        )

    wb = load_workbook(path, read_only=False, data_only=True)
    try:
        ws = wb.worksheets[0]
        sheet_name, _nr, _nc, cells, row_src = _read_sheet_grid_with_sources(ws)
        cells, row_src = trim_leading_empty_rows_paired(cells, row_src)
        merges_src = _collect_output_merges(ws, row_src, _COL_KEEP_LO, _COL_KEEP_HI)
        cells = trim_dg_edge_columns(cells)
    finally:
        wb.close()

    nrows = len(cells)
    ncols = len(cells[0]) if cells and cells[0] is not None else 0
    # 行号不因裁列而变；合并区已在输出坐标上
    merges = [m for m in merges_src if m['c'] + m['cs'] <= ncols and m['r'] + m['rs'] <= nrows]
    header_row = find_tidan_header_row(cells)
    return {
        'title': '4月25日 · DG 周鹏报价表',
        'template_file': os.path.basename(path),
        'sheet_name': sheet_name,
        'rows': nrows,
        'cols': ncols,
        'cells': cells,
        'merges': merges,
        'header_orange_row': header_row,
        'remark': '',
    }


def _fallback_with_meta(hint=''):
    return {
        'title': 'DG 报价表',
        'template_file': '',
        'sheet_name': '',
        'rows': 1,
        'cols': 1,
        'cells': [[hint or '无数据']],
        'merges': [],
        'header_orange_row': None,
        'remark': '',
    }


def fallback_empty_grid(hint=''):
    return _fallback_with_meta(hint)
