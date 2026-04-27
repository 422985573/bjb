# -*- coding: utf-8 -*-
import json
import importlib
import logging
import os
import re
import time
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app, send_file
import uuid

import config
import db
import models
from services import postcode as postcode_svc
from util import admin_required

api_bp = Blueprint('api', __name__, url_prefix='/api')

_POSTCODE_RETRY_ATTEMPTS = 3
_POSTCODE_RETRY_BACKOFF_SECONDS = 0.1
_logger = logging.getLogger(__name__)


def _is_exact_postcode(value):
    return bool(re.fullmatch(r'\d{4}', str(value or '').strip()))


def _json_business(payload):
    return jsonify(payload)


def _load_article_export_service():
    try:
        return importlib.import_module('services.article_export'), None
    except ImportError as exc:
        _logger.exception('article export service unavailable: %s', exc)
        return None, 'ARTICLE_EXPORT_UNAVAILABLE'


def _safe_get_reject_postcodes_by_channel():
    try:
        return postcode_svc.get_reject_postcodes_by_channel(), None
    except Exception:
        return None, 'LOCAL_POSTCODE_DATA_UNAVAILABLE'


def _serialize_channel_postcodes(by_channel):
    return {
        channel: sorted(str(code) for code in codes)
        for channel, codes in (by_channel or {}).items()
    }


def _is_valid_distance_api_success(data):
    if not isinstance(data, dict):
        return False
    payload_data = data.get('data')
    payload_status = data.get('status')
    if payload_status != 'success':
        return False
    if not isinstance(payload_data, dict):
        return False
    return payload_data.get('distance') is not None


def _is_explicit_no_service_payload(data):
    if not isinstance(data, dict):
        return False
    payload_status = (data.get('status') or '').strip().lower()
    if payload_status == 'no_service':
        return True
    if payload_status != 'success':
        return False
    payload_data = data.get('data')
    if not isinstance(payload_data, dict):
        return False
    if 'distance' not in payload_data:
        return False
    return payload_data.get('distance') is None


@api_bp.route('/postcode-channel-status', methods=['GET'])
def postcode_channel_status():
    """公开返回各渠道拒收邮编状态，供前端或健康检查使用。"""
    by_channel, err = _safe_get_reject_postcodes_by_channel()
    if err:
        return jsonify({
            'success': False,
            'message': '渠道邮编数据暂不可用',
            'data': {
                '大件': [],
                '纸箱': [],
            }
        }), 503

    normalized = {
        '大件': by_channel.get('大件', set()),
        '纸箱': by_channel.get('纸箱', set()),
    }
    return jsonify({
        'success': True,
        'data': _serialize_channel_postcodes(normalized),
    })


@api_bp.route('/upload', methods=['POST'])
@admin_required
def upload():
    """上传文件 API（需登录）"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '没有文件'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '没有选择文件'}), 400
    if file and models.allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = datetime.now().strftime('%Y%m%d%H%M%S') + '_' + uuid.uuid4().hex[:8] + '.' + ext
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        return jsonify({'success': True, 'url': f'/uploads/{filename}', 'filename': filename})
    return jsonify({'success': False, 'message': '不支持的文件类型'}), 400


@api_bp.route('/uploaded-images', methods=['GET'])
@admin_required
def uploaded_images():
    """获取已上传的图片列表"""
    try:
        upload_folder = current_app.config['UPLOAD_FOLDER']
        if not os.path.exists(upload_folder):
            return jsonify({'success': True, 'images': []})
        images = []
        for filename in os.listdir(upload_folder):
            if models.allowed_file(filename):
                file_path = os.path.join(upload_folder, filename)
                if os.path.isfile(file_path):
                    stat = os.stat(file_path)
                    images.append({
                        'url': f'/uploads/{filename}',
                        'filename': filename,
                        'size': stat.st_size,
                        'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                    })
        images.sort(key=lambda x: x['modified'], reverse=True)
        return jsonify({'success': True, 'images': images})
    except Exception:
        return jsonify({'success': False, 'message': '获取图片列表失败'}), 500


@api_bp.route('/postcode-evaluate/<postcode>')
def query_postcode_evaluate(postcode):
    """一次性查询邮编距离与大件/纸箱状态；最多 3 次，无服务需跑满 3 次确认。"""
    ua = (request.headers.get('User-Agent') or '').strip()
    attempt_trace = []

    if not _is_exact_postcode(postcode):
        _logger.info('postcode_evaluate invalid_format postcode=%r ua=%r', postcode, ua[:200])
        return _json_business({'status': 'error', 'code': 400, 'message': '邮编格式错误，请输入4位数字邮编'})
    key = postcode
    by_channel, err = _safe_get_reject_postcodes_by_channel()
    if err:
        _logger.warning('postcode_evaluate reject_data_unavailable postcode=%s ua=%r', key, ua[:200])
        return _json_business({'status': 'unavailable', 'message': '服务暂不可用，请稍后重试', 'data': {'postcode': key}})
    dajian_status = 'no_service' if key in by_channel.get('大件', set()) else 'ok'
    zhixiang_status = 'no_service' if key in by_channel.get('纸箱', set()) else 'ok'

    distance_payload = None
    deterministic_no_data_count = 0
    transient_failure_count = 0
    attempt_used = 0

    for attempt in range(1, _POSTCODE_RETRY_ATTEMPTS + 1):
        attempt_used = attempt
        data, err = postcode_svc.fetch_postcode_distance(key)
        if err:
            attempt_trace.append({
                'attempt': attempt,
                'upstream_status': 'fetch_error',
                'distance': None,
            })
            transient_failure_count += 1
            if attempt < _POSTCODE_RETRY_ATTEMPTS:
                time.sleep(_POSTCODE_RETRY_BACKOFF_SECONDS * attempt)
            continue

        payload_data = data.get('data') if isinstance(data, dict) else None
        payload_status = (data.get('status') if isinstance(data, dict) else '') or ''
        payload_distance = payload_data.get('distance') if isinstance(payload_data, dict) else None
        attempt_trace.append({
            'attempt': attempt,
            'upstream_status': payload_status or 'unknown',
            'has_data': isinstance(payload_data, dict),
            'distance': payload_distance,
            'message': (data.get('message') if isinstance(data, dict) else None),
        })

        if payload_status == 'success' and payload_distance is not None:
            distance_payload = data['data']
            break

        # 未查到有效距离时，继续跑满 3 次；只有 3 次都明确无服务，才最终判无服务。
        if _is_explicit_no_service_payload(data):
            deterministic_no_data_count += 1
        else:
            transient_failure_count += 1

        if attempt < _POSTCODE_RETRY_ATTEMPTS:
            time.sleep(_POSTCODE_RETRY_BACKOFF_SECONDS * attempt)

    response_data = {
        'postcode': key,
        'dajian_status': dajian_status,
        'zhixiang_status': zhixiang_status,
        'distance': None,
        'distance_unit': 'km',
        'attempts': _POSTCODE_RETRY_ATTEMPTS,
        'attempts_used': attempt_used
    }

    if distance_payload is not None:
        _logger.info(
            'postcode_evaluate success postcode=%s dajian=%s zhixiang=%s attempts_used=%s trace=%s ua=%r',
            key,
            dajian_status,
            zhixiang_status,
            attempt_used,
            attempt_trace,
            ua[:200],
        )
        return _json_business({
            'status': 'success',
            'data': {
                'postcode': key,
                'dajian_status': dajian_status,
                'zhixiang_status': zhixiang_status,
                'distance': distance_payload.get('distance'),
                'distance_unit': distance_payload.get('distance_unit') or 'km',
                'attempts': _POSTCODE_RETRY_ATTEMPTS,
                'attempts_used': attempt_used
            }
        })

    # 最多只查 3 次；没有查到有效距离时，只有 3 次都明确无服务才返回无服务。
    if deterministic_no_data_count >= _POSTCODE_RETRY_ATTEMPTS:
        _logger.info(
            'postcode_evaluate no_service postcode=%s dajian=%s zhixiang=%s attempts_used=%s trace=%s ua=%r',
            key,
            dajian_status,
            zhixiang_status,
            attempt_used,
            attempt_trace,
            ua[:200],
        )
        return _json_business({
            'status': 'no_service',
            'message': '无服务',
            'data': response_data
        })

    _logger.warning(
        'postcode_evaluate unavailable postcode=%s dajian=%s zhixiang=%s attempts_used=%s deterministic_no_data_count=%s transient_failure_count=%s trace=%s ua=%r',
        key,
        dajian_status,
        zhixiang_status,
        attempt_used,
        deterministic_no_data_count,
        transient_failure_count,
        attempt_trace,
        ua[:200],
    )
    return _json_business({
        'status': 'unavailable',
        'message': '查询服务暂时不可用，请稍后再试',
        'data': response_data
    })


@api_bp.route('/module/add', methods=['POST'])
@admin_required
def module_add():
    """添加模块"""
    data = request.json
    article_id = data.get('article_id')
    module_type = data.get('type')
    content = data.get('content', '')
    if module_type == 'dg_grid':
        c = content if isinstance(content, dict) else {}
        # 新模块：默认使用空白网格，不从本地 xlsx 导入；若显式带 cells 则保留（高级复制等）
        cells_in = c.get("cells")
        if isinstance(cells_in, list) and len(cells_in) > 0:
            content = c
        else:
            from services.dg_quote_grid import empty_dg_grid_content

            content = empty_dg_grid_content()
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(sort_order) FROM modules WHERE article_id = ?', (article_id,))
        max_order = cursor.fetchone()[0] or 0
        cursor.execute(
            'INSERT INTO modules (article_id, type, content, sort_order) VALUES (?, ?, ?, ?)',
            (article_id, module_type, json.dumps(content, ensure_ascii=False), max_order + 1)
        )
        module_id = cursor.lastrowid
        conn.commit()
        return jsonify({'success': True, 'module_id': module_id})


@api_bp.route('/module/<int:module_id>/update', methods=['POST'])
@admin_required
def module_update(module_id):
    """更新模块"""
    data = request.json
    content = data.get('content')
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE modules SET content = ? WHERE id = ?', (json.dumps(content, ensure_ascii=False), module_id))
        conn.commit()
        return jsonify({'success': True})


@api_bp.route('/module/<int:module_id>/delete', methods=['POST'])
@admin_required
def module_delete(module_id):
    """删除模块"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM modules WHERE id = ?', (module_id,))
        conn.commit()
        return jsonify({'success': True})


@api_bp.route('/modules/reorder', methods=['POST'])
@admin_required
def modules_reorder():
    """重新排序模块"""
    data = request.json
    module_orders = data.get('orders', [])
    with db.get_db() as conn:
        cursor = conn.cursor()
        for i, module_id in enumerate(module_orders):
            cursor.execute('UPDATE modules SET sort_order = ? WHERE id = ?', (i, module_id))
        conn.commit()
        return jsonify({'success': True})


@api_bp.route('/article/<int:article_id>/save_all', methods=['POST'])
@admin_required
def article_save_all(article_id):
    """一键保存文章信息和所有模块"""
    try:
        data = request.json or {}
        title = data.get('title')
        category_id = data.get('category_id')
        is_published = data.get('is_published')
        modules = data.get('modules', [])
        with db.get_db() as conn:
            cursor = conn.cursor()
            if title is not None or category_id is not None or is_published is not None:
                cursor.execute('SELECT title, category_id, is_published FROM articles WHERE id = ?', (article_id,))
                article_row = cursor.fetchone()
                if not article_row:
                    return jsonify({'success': False, 'message': '文章不存在'}), 404

                next_title = article_row['title'] if title is None else title
                next_category_id = article_row['category_id'] if category_id is None else category_id
                next_is_published = article_row['is_published'] if is_published is None else (1 if str(is_published) == '1' else 0)
                cursor.execute(
                    'UPDATE articles SET title = ?, category_id = ?, is_published = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                    (next_title, next_category_id, next_is_published, article_id)
                )
            for module in modules:
                module_id = module.get('id')
                content = module.get('content')
                if not module_id:
                    continue
                cursor.execute(
                    'UPDATE modules SET content = ? WHERE id = ?',
                    (json.dumps(content, ensure_ascii=False), module_id)
                )
            conn.commit()
            return jsonify({'success': True})
    except Exception:
        return jsonify({'success': False, 'message': '保存失败，请稍后重试'}), 500


@api_bp.route('/article/<int:article_id>/publish-status', methods=['POST'])
@admin_required
def article_publish_status(article_id):
    """切换文章发布状态"""
    data = request.json or {}
    is_published = data.get('is_published')
    if is_published in (True, False):
        is_published = 1 if is_published else 0
    try:
        is_published = int(is_published)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '发布状态参数无效'}), 400
    if is_published not in (0, 1):
        return jsonify({'success': False, 'message': '发布状态参数无效'}), 400

    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE articles SET is_published = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (is_published, article_id)
        )
        if cursor.rowcount == 0:
            return jsonify({'success': False, 'message': '文章不存在'}), 404
        conn.commit()
    return jsonify({'success': True, 'is_published': is_published})


@api_bp.route('/category/add', methods=['POST'])
@admin_required
def category_add():
    """添加分类"""
    data = request.json
    name = data.get('name')
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(sort_order) FROM categories')
        max_order = cursor.fetchone()[0] or 0
        cursor.execute('INSERT INTO categories (name, sort_order) VALUES (?, ?)', (name, max_order + 1))
        category_id = cursor.lastrowid
        conn.commit()
        return jsonify({'success': True, 'category_id': category_id})


@api_bp.route('/category/<int:category_id>/update', methods=['POST'])
@admin_required
def category_update(category_id):
    """编辑分类"""
    data = request.json or {}
    name = data.get('name')
    sort_order = data.get('sort_order')
    with db.get_db() as conn:
        cursor = conn.cursor()
        if name is not None:
            cursor.execute('UPDATE categories SET name = ? WHERE id = ?', (name.strip(), category_id))
        if sort_order is not None:
            cursor.execute('UPDATE categories SET sort_order = ? WHERE id = ?', (int(sort_order), category_id))
        conn.commit()
        return jsonify({'success': True})


@api_bp.route('/category/<int:category_id>/delete', methods=['POST'])
@admin_required
def category_delete(category_id):
    """删除分类：仅当分类下没有文章时才允许删除"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM categories WHERE id = ?', (category_id,))
        if cursor.fetchone()[0] == 0:
            return jsonify({'success': False, 'message': '分类不存在'}), 404

        cursor.execute('SELECT COUNT(*) FROM articles WHERE category_id = ?', (category_id,))
        if cursor.fetchone()[0] > 0:
            return jsonify({'success': False, 'message': '该分类下已有文章，不能删除'}), 400

        cursor.execute('DELETE FROM categories WHERE id = ?', (category_id,))
        conn.commit()
        return jsonify({'success': True})


@api_bp.route('/channel-postcodes', methods=['GET'])
@admin_required
def channel_postcodes_get():
    """获取渠道拒收邮编"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT channel, postcodes FROM channel_reject_postcodes ORDER BY channel')
        rows = cursor.fetchall()
        data = {row[0]: (row[1] or '') for row in rows}
        return jsonify({'success': True, 'data': data})


@api_bp.route('/channel-postcodes', methods=['POST'])
@admin_required
def channel_postcodes_save():
    """保存渠道拒收邮编"""
    data = request.json or {}
    with db.get_db() as conn:
        cursor = conn.cursor()
        for channel in ('大件', '纸箱'):
            raw = data.get(channel, '')
            if not isinstance(raw, str):
                raw = str(raw)
            parts = raw.replace(',', '\n').split('\n')
            codes = []
            for part in parts:
                code = ''.join(c for c in part.strip() if c.isdigit())
                if len(code) == 4:
                    codes.append(code)
            postcodes_str = ','.join(codes)
            cursor.execute(
                'INSERT OR REPLACE INTO channel_reject_postcodes (channel, postcodes, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)',
                (channel, postcodes_str)
            )
        conn.commit()
        return jsonify({'success': True})


@api_bp.route('/article/<article_code>/export-xlsx', methods=['GET'])
def export_article_xlsx(article_code):
    """导出文章详情为 xlsx。"""
    article_export, err = _load_article_export_service()
    if err:
        return jsonify({'success': False, 'message': '导出功能暂不可用，请检查服务器依赖安装'}), 503

    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT a.*, c.name as category_name
            FROM articles a
            LEFT JOIN categories c ON a.category_id = c.id
            WHERE a.article_code = ?
        ''', (article_code,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'success': False, 'message': '文章不存在'}), 404
        article = dict(row)
        cursor.execute('SELECT * FROM modules WHERE article_id = ? ORDER BY sort_order', (article['id'],))
        modules = [dict(module) for module in cursor.fetchall()]

    workbook = article_export.build_article_workbook(
        article=article,
        modules=modules,
        exported_at_text=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    )
    output = article_export.workbook_to_bytes(workbook)
    safe_title = re.sub(r'[\\/:*?"<>|]+', '-', article.get('title') or '文章详情').strip() or '文章详情'
    filename = f'{safe_title}-表格导出.xlsx'
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
