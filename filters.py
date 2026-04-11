# -*- coding: utf-8 -*-
"""Jinja2 自定义过滤器与模板上下文"""
import json
import os
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


def safe_html_filter(value):
    """富文本白名单过滤，降低 XSS 风险"""
    if not value:
        return ''
    sanitizer = _BasicHTMLSanitizer(ALLOWED_TAGS_HTML, ALLOWED_ATTRS_HTML)
    sanitizer.feed(str(value))
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


def register_filters(app):
    """在 Flask 应用上注册上述过滤器与上下文"""
    app.jinja_env.filters['from_json'] = from_json_filter
    app.jinja_env.filters['safe_html'] = safe_html_filter
    app.jinja_env.filters['format_postcode'] = format_postcode_filter

    @app.context_processor
    def inject_static_version():
        return {'static_with_version': _static_with_version}
