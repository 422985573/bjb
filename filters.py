# -*- coding: utf-8 -*-
"""Jinja2 自定义过滤器与模板上下文"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

from markupsafe import Markup, escape

ALLOWED_TAGS_HTML = [
    'p', 'br', 'span', 'div', 'strong', 'b', 'em', 'i', 'u', 's',
    'ul', 'ol', 'li', 'a', 'img', 'h1', 'h2', 'h3', 'h4', 'blockquote',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
]
ALLOWED_ATTRS_HTML = {
    'a': ['href', 'title', 'target', 'rel', 'class', 'style'],
    'img': ['src', 'alt', 'title', 'width', 'height', 'class', 'style'],
    'p': ['class', 'style', 'align'],
    'div': ['class', 'style', 'align'],
    'span': ['class', 'style', 'align'],
    'h1': ['class', 'style', 'align'],
    'h2': ['class', 'style', 'align'],
    'h3': ['class', 'style', 'align'],
    'h4': ['class', 'style', 'align'],
    'blockquote': ['class', 'style', 'align'],
    'ul': ['class', 'style'],
    'ol': ['class', 'style'],
    'li': ['class', 'style'],
    'table': ['class', 'style'],
    'thead': ['class', 'style'],
    'tbody': ['class', 'style'],
    'tr': ['class', 'style'],
    'th': ['class', 'style', 'colspan', 'rowspan', 'scope'],
    'td': ['class', 'style', 'colspan', 'rowspan'],
}
ALLOWED_STYLE_PROPERTIES = {
    'background', 'background-color', 'border', 'border-bottom', 'border-left', 'border-right', 'border-top',
    'border-radius', 'color', 'display', 'float', 'clear', 'font-family', 'font-size', 'font-style',
    'font-weight', 'height', 'letter-spacing', 'line-height', 'margin', 'margin-bottom', 'margin-left',
    'margin-right', 'margin-top', 'max-width', 'min-height', 'min-width', 'padding', 'padding-bottom',
    'padding-left', 'padding-right', 'padding-top', 'text-align', 'text-decoration', 'text-indent',
    'vertical-align', 'white-space', 'width'
}


class _BasicHTMLSanitizer(HTMLParser):
    """当 bleach 不可用时，使用标准库做基础白名单清洗。"""

    VOID_TAGS = {'br', 'img'}

    def __init__(self, allowed_tags, allowed_attrs):
        super().__init__(convert_charrefs=True)
        self.allowed_tags = set(allowed_tags)
        self.allowed_attrs = allowed_attrs
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed_tags:
            return
        rendered_attrs = []
        for name, value in attrs:
            allowed = self.allowed_attrs.get(tag, [])
            if name not in allowed:
                continue
            if name == 'style':
                style_value = self._sanitize_style(value or '')
                if not style_value:
                    continue
                rendered_attrs.append(f' style="{escape(style_value)}"')
                continue
            if name in {'href', 'src'} and not self._is_safe_url(value or ''):
                continue
            rendered_attrs.append(f' {name}="{escape(value or "")}"')
        if tag in self.VOID_TAGS:
            self.parts.append(f'<{tag}{"".join(rendered_attrs)}>') 
        else:
            self.parts.append(f'<{tag}{"".join(rendered_attrs)}>')

    def handle_endtag(self, tag):
        if tag in self.allowed_tags and tag not in self.VOID_TAGS:
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        self.parts.append(str(escape(data)))

    def handle_entityref(self, name):
        self.parts.append(f'&{name};')

    def handle_charref(self, name):
        self.parts.append(f'&#{name};')

    def get_html(self):
        return ''.join(self.parts)

    def _sanitize_style(self, value):
        declarations = []
        for chunk in str(value or '').split(';'):
            if ':' not in chunk:
                continue
            prop, raw_val = chunk.split(':', 1)
            prop_name = prop.strip().lower()
            clean_value = raw_val.strip()
            if not prop_name or prop_name not in ALLOWED_STYLE_PROPERTIES:
                continue
            lowered = clean_value.lower()
            if 'expression(' in lowered or 'javascript:' in lowered or 'url(' in lowered:
                continue
            declarations.append(f'{prop_name}: {clean_value}')
        return '; '.join(declarations)

    def _is_safe_url(self, value):
        raw = str(value or '').strip().lower()
        if raw.startswith(('http://', 'https://', '/', './', '../', 'mailto:', 'tel:')):
            return True
        return False


def from_json_filter(value):
    """将 JSON 字符串转换为 Python 对象"""
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


_EMPTY_P_RE = re.compile(r'<p[^>]*>\s*(?:<br\s*/?>|&nbsp;| |\s)?\s*</p>', re.IGNORECASE)
_P_LEADING_BR_RE = re.compile(r'<p([^>]*)>\s*<br\s*/?>', re.IGNORECASE)
_P_TRAILING_BR_RE = re.compile(r'<br\s*/?>\s*</p>', re.IGNORECASE)


def _clean_richtext_html(value):
    """去掉富文本里 wangEditor 加粗/换色时残留的空段落和首尾 <br>"""
    if not value:
        return value
    text = str(value)
    while True:
        new_text = _EMPTY_P_RE.sub('', text)
        if new_text == text:
            break
        text = new_text
    text = _P_LEADING_BR_RE.sub(r'<p\1>', text)
    text = _P_TRAILING_BR_RE.sub('</p>', text)
    return text


def safe_html_filter(value):
    """富文本白名单过滤，降低 XSS 风险"""
    if not value:
        return ''
    cleaned = _clean_richtext_html(value)
    sanitizer = _BasicHTMLSanitizer(ALLOWED_TAGS_HTML, ALLOWED_ATTRS_HTML)
    sanitizer.feed(str(cleaned))
    sanitizer.close()
    return Markup(sanitizer.get_html())


def format_postcode_filter(value):
    """格式化邮编显示，限制长度"""
    if not value:
        return ''
    value = str(value).strip()
    if '~' in value or '-' in value:
        return value
    if ',' in value:
        postcodes = [p.strip() for p in value.split(',') if p.strip()]
        if len(postcodes) <= 4:
            return value
        front = ','.join(postcodes[:1])
        back = ','.join(postcodes[-2:])
        return f"{front},...,{back}"
    if len(value) > 50:
        return value[:47] + '...'
    return value


def format_datetime_beijing_filter(value, fmt='%Y-%m-%d %H:%M'):
    """将数据库中的 UTC 时间按北京时间展示。"""
    if not value:
        return ''
    raw = str(value).strip()
    if not raw:
        return ''
    parsed = None
    for pattern in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            parsed = datetime.strptime(raw, pattern).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            continue
    if parsed is None:
        return raw[:16] if len(raw) >= 16 else raw
    beijing_time = parsed.astimezone(timezone(timedelta(hours=8)))
    return beijing_time.strftime(fmt)


def _static_with_version(filename):
    """返回带 ?v=文件修改时间 的静态 URL，用于避免 CSS/JS 缓存不更新"""
    from flask import url_for, current_app
    base = url_for('static', filename=filename)
    path = os.path.join(current_app.static_folder or '', filename)
    if os.path.isfile(path):
        try:
            return base + '?v=' + str(int(os.path.getmtime(path)))
        except OSError:
            pass
    return base


def dg_grid_trim_leading_filter(cells):
    """dg_grid：去掉数据矩阵顶部全空行（模板展示/编辑用）。"""
    if not cells:
        return cells or []
    from services.dg_quote_grid import trim_leading_empty_rows

    return trim_leading_empty_rows(cells)


def dg_grid_normalize_filter(cells):
    """dg_grid：去顶空行 + 去掉第 1 列与最后 3 列（与后台默认导入一致）。"""
    from services.dg_quote_grid import normalize_dg_grid_cells

    return normalize_dg_grid_cells(cells or [])


def dg_v2_merged_filter(module_content):
    """后台编辑：补全 DG v2 缺省字段。"""
    from services.dg_quote_grid import merge_dg_v2_content

    c = module_content
    if c is None:
        c = {}
    if isinstance(c, str):
        c = from_json_filter(c)
    if not isinstance(c, dict):
        c = {}
    return merge_dg_v2_content(c)


def dg_excel_grid_canonical_filter(content):
    """Excel 栅格（非 v2）：前台展示用规范化副本（表格裁列/免责并入备注），不写库。"""
    from services.dg_quote_grid import canonical_excel_grid_module_content

    c = content
    if c is None:
        return {}
    if isinstance(c, str):
        c = from_json_filter(c)
    if not isinstance(c, dict):
        return {}
    return canonical_excel_grid_module_content(c)


def dg_grid_render_table_filter(module_content):
    """dg_grid：v2 为分块版式；旧数据为合并 <table>。"""
    from markupsafe import Markup

    from services.dg_quote_grid import (
        find_excel_quote_fee_header_row,
        prepare_dg_table_display,
        render_dg_excel_grid_split_document_html,
        render_dg_quote_v2_html,
        render_dg_table_html,
        is_dg_content_v2,
    )

    c = module_content
    if c is None:
        return Markup('')
    if isinstance(c, str):
        c = from_json_filter(c)
    if not isinstance(c, dict):
        return Markup('')

    if is_dg_content_v2(c):
        return render_dg_quote_v2_html(c)

    cells, merges, hr = prepare_dg_table_display(c)
    fee_split_row = find_excel_quote_fee_header_row(cells)
    if fee_split_row is not None:
        return render_dg_excel_grid_split_document_html(cells, merges, hr, fee_split_row)

    inner = render_dg_table_html(cells, merges, hr)
    return Markup(
        '<div class="dg-quote-v2 dg-quote-v2--grid dg-excel-quote-layout">'
        '<div class="dg-quote-v2-section dg-quote-v2-fee-block"><div class="dg-quote-v2-fee-wrap">'
        f'{inner}</div></div></div>'
    )


def dg_grid_editable_table_filter(module_content):
    """DG 模块后台编辑：Excel 栅格分段（抬头 / 费用）；其余为合并表格。"""
    from markupsafe import Markup

    from services.dg_quote_grid import (
        find_excel_quote_fee_header_row,
        prepare_dg_table_display,
        render_dg_excel_grid_editable_split_html,
        render_dg_table_editable_html,
    )

    c = module_content
    if c is None:
        return Markup('')
    if isinstance(c, str):
        c = from_json_filter(c)
    if not isinstance(c, dict):
        return Markup('')

    cells, merges, hr = prepare_dg_table_display(c)
    variant = str((c.get('variant') or '')).strip().lower()
    if variant == 'excel_grid' and find_excel_quote_fee_header_row(cells) is not None:
        return Markup(render_dg_excel_grid_editable_split_html(cells, merges, hr))
    # 非柜类模板（Freightconn 等）的 excel_grid：首列作为主单锚点，允许单行主单也生成「添加」按钮，
    # 首行按约定为多列表头（CODE/UOM 等）：仍视为表头（不算主单、不显示删除/添加按钮），但允许编辑表头文字。
    tid = str((c.get('template_id') or '')).strip()
    if variant == 'excel_grid' and tid not in ('9类电池柜', '普柜'):
        return Markup(render_dg_table_editable_html(
            cells, merges, hr,
            fee_group_anchor_col=0,
            single_row_groups=True,
            group_header_rows={0},
            group_add_row_label='添加',
            group_delete_label='删除',
        ))
    return Markup(render_dg_table_editable_html(cells, merges, hr))


def dg_grid_content_dimensions_filter(module_content):
    """与 prepare_dg_table_display 一致裁剪后的行数、列数（后台 dg-grid-editor dataset）。"""
    from services.dg_quote_grid import prepare_dg_table_display

    c = module_content
    if c is None:
        return {"rows": 1, "cols": 1}
    if isinstance(c, str):
        c = from_json_filter(c)
    if not isinstance(c, dict):
        return {"rows": 1, "cols": 1}
    cells, _merges, _hr = prepare_dg_table_display(c)
    n = len(cells)
    m = max((len(r) for r in cells), default=1) if n else 1
    return {"rows": n, "cols": m}


def register_filters(app):
    """在 Flask 应用上注册上述过滤器与上下文"""
    app.jinja_env.filters['from_json'] = from_json_filter
    app.jinja_env.filters['safe_html'] = safe_html_filter
    app.jinja_env.filters['format_postcode'] = format_postcode_filter
    app.jinja_env.filters['format_datetime_beijing'] = format_datetime_beijing_filter
    app.jinja_env.filters['dg_grid_trim_leading'] = dg_grid_trim_leading_filter
    app.jinja_env.filters['dg_grid_normalize'] = dg_grid_normalize_filter
    app.jinja_env.filters['dg_v2_merged'] = dg_v2_merged_filter
    app.jinja_env.filters['dg_excel_grid_canonical'] = dg_excel_grid_canonical_filter
    app.jinja_env.filters['dg_grid_render_table'] = dg_grid_render_table_filter
    app.jinja_env.filters['dg_grid_editable_table'] = dg_grid_editable_table_filter
    app.jinja_env.filters['dg_grid_content_dimensions'] = dg_grid_content_dimensions_filter

    @app.context_processor
    def inject_static_version():
        return {'static_with_version': _static_with_version}
