# -*- coding: utf-8 -*-
"""通用工具：如管理员鉴权装饰器"""
from functools import wraps

from flask import session, request, jsonify, redirect, url_for


def admin_required(f):
    """管理员登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            if request.path.startswith('/api/') or request.is_json or (
                request.accept_mimetypes.best and 'application/json' in str(request.accept_mimetypes)
            ):
                return jsonify({'success': False, 'message': '请先登录或会话已过期'}), 401
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated_function
