# -*- coding: utf-8 -*-
"""Jinja2 自定义过滤器与模板上下文"""
import json
import os

try:
    import bleach
except ImportError:
    bleach = None

ALLOWED_TAGS_HTML = [
    'p', 'br', 'span', 'div', 'strong', 'b', 'em', 'i', 'u', 's',
    'ul', 'ol', 'li', 'a', 'img', 'h1', 'h2', 'h3', 'h4', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
]
ALLOWED_ATTRS_HTML = {'a': ['href', 'title'], 'img': ['src', 'alt', 'title']}


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
    if bleach is None:
        from markupsafe import escape
        return escape(value)
    return bleach.clean(str(value), tags=ALLOWED_TAGS_HTML, attributes=ALLOWED_ATTRS_HTML, strip=True)


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
