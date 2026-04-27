# -*- coding: utf-8 -*-
"""从 DG 报价 xlsx 加载默认可编辑网格（用于 dg_grid 模块）。"""
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
        parts.append('</tr>')
    parts.append('</tbody></table>')
    return ''.join(parts)


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
