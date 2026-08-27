# -*- coding: utf-8 -*-
import json
import functools
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


def _module_content_json_for_db(content):
    """写入 modules.content 前：Excel 栅格规范化；DG v2 将 PS 说明行并入备注。"""
    if isinstance(content, dict):
        from services.dg_quote_grid import normalize_dg_v2_ps_rows_to_remark, normalize_excel_grid_content_storage

        if int(content.get("schema") or 0) == 2:
            content = normalize_dg_v2_ps_rows_to_remark(dict(content))
        else:
            content = normalize_excel_grid_content_storage(content)
    return json.dumps(content, ensure_ascii=False)


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
        # 新模块：默认 v2(DG报价) 或 Excel栅格（9类电池柜 / 普柜等）；显式带 cells 则保留（高级复制等）
        cells_in = c.get("cells")
        variant = (c.get("variant") or "").strip().lower()
        if isinstance(cells_in, list) and len(cells_in) > 0:
            content = c
        elif variant == "excel_grid":
            hint = (c.get("title") or "") if isinstance(c.get("title"), str) else ""
            hint_tid = ((c.get("template_id") or "") if isinstance(c.get("template_id"), str) else "").strip()
            # 若 template_id 命中已注册模板，优先从 xlsx 导入（与「种子脚本」和实际数据一致）
            _loaded_from_xlsx = False
            if hint_tid:
                from services.dg_excel_template_registry import EXCEL_QUOTE_TEMPLATE_ENTRIES
                from services.dg_quote_grid import cabinet_quote_xlsx_grid_content
                import config as _cfg
                for _entry in EXCEL_QUOTE_TEMPLATE_ENTRIES:
                    if _entry["id"] == hint_tid:
                        _src = _entry["source_xlsx"]
                        _abs = _src if os.path.isabs(_src) else os.path.join(_cfg._BASE_DIR, _src)
                        if os.path.isfile(_abs):
                            content = cabinet_quote_xlsx_grid_content(_abs)
                            _loaded_from_xlsx = True
                        break
            if not _loaded_from_xlsx:
                from services.dg_quote_grid import empty_dg_excel_grid_content
                content = empty_dg_excel_grid_content(
                    title=hint.strip() or None,
                    template_id=hint_tid or None,
                )
        else:
            from services.dg_quote_grid import empty_dg_grid_content

            content = empty_dg_grid_content()
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(sort_order) FROM modules WHERE article_id = ?', (article_id,))
        max_order = cursor.fetchone()[0] or 0
        db_content = (
            _module_content_json_for_db(content)
            if isinstance(content, dict)
            else json.dumps(content, ensure_ascii=False)
        )
        cursor.execute(
            'INSERT INTO modules (article_id, type, content, sort_order) VALUES (?, ?, ?, ?)',
            (article_id, module_type, db_content, max_order + 1),
        )
        module_id = cursor.lastrowid
        conn.commit()
        return jsonify({'success': True, 'module_id': module_id})


@api_bp.route('/dg/excel_editor_reload', methods=['POST'])
@admin_required
def dg_excel_editor_reload():
    """Excel 栅格：根据 cells/merges 重渲染当前编辑器片段。
    - 若 cells 里含「费用项目」+「币别/币种」或「提单号」，走分段抬头/费用编辑器；
    - 否则走非分段的整表编辑器（Freightconn 类），并把主单锚点参数一并透出。
    """
    from services.dg_quote_grid import (
        find_excel_quote_fee_header_row,
        render_dg_excel_grid_editable_split_html,
        render_dg_table_editable_html,
    )

    d = request.json or {}
    cells = d.get('cells')
    merges = d.get('merges')
    hr = d.get('header_orange_row')
    mode = str(d.get('mode') or '').strip().lower()
    if not isinstance(cells, list):
        cells = []
    if not isinstance(merges, list):
        merges = []
    is_split = mode == 'split' or (mode != 'plain' and find_excel_quote_fee_header_row(cells) is not None)
    if is_split:
        html = render_dg_excel_grid_editable_split_html(cells, merges, hr)
    else:
        html = render_dg_table_editable_html(
            cells, merges, hr,
            fee_group_anchor_col=0,
            single_row_groups=True,
            group_header_rows={0},
            readonly_row_set={0},
            group_add_row_label='添加',
            group_delete_label='删除',
        )
    nrows = len(cells)
    ncols = max((len(r) for r in cells), default=1) if cells else 1
    return jsonify({
        'success': True,
        'html': html,
        'mode': 'split' if is_split else 'plain',
        'merges': merges,
        'header_orange_row': hr,
        'rows': nrows,
        'cols': ncols,
    })


@api_bp.route('/module/<int:module_id>/update', methods=['POST'])
@admin_required
def module_update(module_id):
    """更新模块"""
    data = request.json
    content = data.get('content')
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE modules SET content = ? WHERE id = ?', (_module_content_json_for_db(content), module_id))
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
                    (_module_content_json_for_db(content), module_id),
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


@api_bp.route('/article/<int:article_id>/auth-status', methods=['POST'])
@admin_required
def article_auth_status(article_id):
    """切换文章是否需要手机号鉴权"""
    data = request.json or {}
    requires_auth = data.get('requires_phone_auth')
    if requires_auth in (True, False):
        requires_auth = 1 if requires_auth else 0
    try:
        requires_auth = int(requires_auth)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '鉴权状态参数无效'}), 400
    if requires_auth not in (0, 1):
        return jsonify({'success': False, 'message': '鉴权状态参数无效'}), 400

    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE articles SET requires_phone_auth = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (requires_auth, article_id)
        )
        if cursor.rowcount == 0:
            return jsonify({'success': False, 'message': '文章不存在'}), 404
        conn.commit()
    return jsonify({'success': True, 'requires_phone_auth': requires_auth})


def _normalize_phone(value):
    return ''.join(c for c in str(value or '') if c.isdigit())


@api_bp.route('/phone-whitelist', methods=['GET'])
@admin_required
def phone_whitelist_list():
    """获取手机号池"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, phone, name, created_at FROM phone_whitelist ORDER BY created_at DESC, id DESC')
        rows = [dict(r) for r in cursor.fetchall()]
        return jsonify({'success': True, 'data': rows})


@api_bp.route('/phone-whitelist/add', methods=['POST'])
@admin_required
def phone_whitelist_add():
    """添加手机号到白名单"""
    data = request.json or {}
    phone = _normalize_phone(data.get('phone'))
    name = (data.get('name') or '').strip()
    if not phone:
        return jsonify({'success': False, 'message': '手机号不能为空'}), 400
    if len(phone) < 6 or len(phone) > 20:
        return jsonify({'success': False, 'message': '手机号长度不正确'}), 400
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM phone_whitelist WHERE phone = ?', (phone,))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': '该手机号已存在'}), 400
        cursor.execute(
            'INSERT INTO phone_whitelist (phone, name) VALUES (?, ?)',
            (phone, name)
        )
        new_id = cursor.lastrowid
        conn.commit()
        return jsonify({'success': True, 'id': new_id, 'phone': phone, 'name': name})


@api_bp.route('/phone-whitelist/<int:phone_id>/delete', methods=['POST'])
@admin_required
def phone_whitelist_delete(phone_id):
    """删除手机号"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM phone_whitelist WHERE id = ?', (phone_id,))
        if cursor.rowcount == 0:
            return jsonify({'success': False, 'message': '手机号不存在'}), 404
        conn.commit()
        return jsonify({'success': True})


@api_bp.route('/article/<article_code>/verify-phone', methods=['POST'])
def article_verify_phone(article_code):
    """公开接口：校验手机号是否在白名单中；通过则在 session 标记本次会话已解锁此文章。"""
    from flask import session
    data = request.json or {}
    phone = _normalize_phone(data.get('phone'))
    if not phone:
        return jsonify({'success': False, 'message': '请输入手机号'}), 400
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM articles WHERE article_code = ?', (article_code,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'message': '文章不存在'}), 404
        cursor.execute('SELECT id, name FROM phone_whitelist WHERE phone = ?', (phone,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'success': False, 'message': '手机号未授权，请联系管理员'}), 403
    verified = list(session.get('phone_verified_articles') or [])
    if article_code not in verified:
        verified.append(article_code)
        session['phone_verified_articles'] = verified
    return jsonify({'success': True})


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
        cursor.execute(
            'INSERT INTO categories (name, sort_order) VALUES (?, ?)',
            (name, max_order + 1),
        )
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


# ---------------------------------------------------------------------------
# 澳洲海外仓报价 — sheet 数据查询 + 批量调价
# ---------------------------------------------------------------------------

def _wh_dir(dirname=None):
    """按文章的 data_dir 定位海外仓价格表目录；白名单校验，空/非法回退默认 'warehouse_au'。"""
    safe = re.sub(r'[^a-zA-Z0-9_]', '', dirname or '')
    if not safe:
        safe = 'warehouse_au'
    return os.path.join(config._BASE_DIR, 'data', safe)


def _wh_index_path(dirname=None):
    return os.path.join(_wh_dir(dirname), '_index.json')


def _wh_sheet_path(key, dirname=None):
    safe = re.sub(r'[^a-zA-Z0-9_]', '', key)
    return os.path.join(_wh_dir(dirname), f'{safe}.json')


def _wh_index_keys(dirname=None):
    """该目录 _index.json 中的全部 sheet key（含副本）。读失败回退 4 张原始表。"""
    path = _wh_index_path(dirname)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            idx = json.load(f)
        keys = [e.get('key') for e in idx if isinstance(e, dict) and e.get('key')]
        return keys or list(_WH_SHEET_KEYS)
    except (ValueError, OSError):
        return list(_WH_SHEET_KEYS)


@api_bp.route('/warehouse-sheets')
def warehouse_sheets_index():
    path = _wh_index_path(request.args.get('dir'))
    if not os.path.isfile(path):
        return jsonify({'success': False, 'message': '数据未初始化'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        index = json.load(f)
    index = [s for s in index if s.get('key') != 'mulu']
    return jsonify({'success': True, 'data': index})


@functools.lru_cache(maxsize=1)
def _load_all_postcode_maps():
    """Load all postcode_zone_map / postcode_zone_maps from sheet JSON files."""
    index_path = _wh_index_path()
    if not os.path.isfile(index_path):
        return []
    with open(index_path, 'r', encoding='utf-8') as f:
        index = json.load(f)
    result = []
    for entry in index:
        key = entry.get('key', '')
        if key == 'mulu':
            continue
        path = _wh_sheet_path(key)
        if not os.path.isfile(path):
            continue
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        name = data.get('name', entry.get('name', key))
        pzm = data.get('postcode_zone_map')
        pzms = data.get('postcode_zone_maps')
        if pzm or pzms:
            result.append({'key': key, 'name': name, 'pzm': pzm, 'pzms': pzms})
    return result


@api_bp.route('/warehouse-postcode-lookup')
def warehouse_postcode_lookup():
    code = (request.args.get('code') or '').strip()
    if not code or not re.fullmatch(r'\d{3,4}', code):
        return jsonify({'success': False, 'message': '请输入3-4位邮编'}), 400

    maps = _load_all_postcode_maps()
    hits = []
    for m in maps:
        if m['pzm'] and code in m['pzm']:
            hits.append({'key': m['key'], 'name': m['name'], 'zone': m['pzm'][code]})
        if m['pzms']:
            for wh_key, wh_map in m['pzms'].items():
                if code in wh_map:
                    hits.append({'key': m['key'], 'name': m['name'], 'warehouse': wh_key, 'zone': wh_map[code]})
    return jsonify({'success': True, 'data': hits})


@api_bp.route('/warehouse-sheet/<key>')
def warehouse_sheet_detail(key):
    path = _wh_sheet_path(key, request.args.get('dir'))
    if not os.path.isfile(path):
        return jsonify({'success': False, 'message': f'sheet "{key}" 不存在'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # slim=1：剔除上万行 zone_table，只回 price_table/richtext + postcode_zone_map（供统一搜索页按邮编查分区）。
    if request.args.get('slim'):
        data['sections'] = [s for s in data.get('sections', []) if s.get('type') != 'zone_table']

    search = (request.args.get('search') or '').strip().lower()
    if search:
        for sec in data.get('sections', []):
            if sec.get('type') != 'zone_table':
                continue
            filtered = []
            for row in sec.get('rows', []):
                if any(search in str(c).lower() for c in row):
                    filtered.append(row)
            sec['rows'] = filtered

    return jsonify({'success': True, 'data': data})


@api_bp.route('/warehouse-adjust-price', methods=['POST'])
@admin_required
def warehouse_adjust_price():
    body = request.json or {}
    sheet_key = body.get('sheet_key', '')
    pct_raw = body.get('percentage')

    if not sheet_key or pct_raw is None:
        return jsonify({'success': False, 'message': '缺少 sheet_key 或 percentage'}), 400

    try:
        pct = float(pct_raw)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'percentage 必须是数字'}), 400

    if abs(pct) > 100:
        return jsonify({'success': False, 'message': '调幅不能超过 ±100%'}), 400

    path = _wh_sheet_path(sheet_key)
    if not os.path.isfile(path):
        return jsonify({'success': False, 'message': f'sheet "{sheet_key}" 不存在'}), 404

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    factor = 1 + pct / 100.0
    adjusted = 0

    for sec in data.get('sections', []):
        if sec.get('type') == 'zone_table':
            continue
        for row in sec.get('rows', []):
            for ci, cell in enumerate(row):
                if cell == '' or cell is None:
                    continue
                try:
                    num = float(cell)
                except (ValueError, TypeError):
                    continue
                new_val = round(num * factor, 2)
                if new_val == int(new_val):
                    new_val = int(new_val)
                row[ci] = new_val
                adjusted += 1

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))

    _load_all_postcode_maps.cache_clear()
    _logger.info('warehouse adjust price sheet=%s pct=%s adjusted=%s', sheet_key, pct, adjusted)
    return jsonify({'success': True, 'adjusted_count': adjusted})


@api_bp.route('/warehouse-sheet/<key>/rename', methods=['POST'])
@admin_required
def warehouse_sheet_rename(key):
    """重命名海外仓价格表标题（前台卡片 H2 / 大标题取自 sheet.name）。
    同时写回该目录 _index.json 与对应 sheet JSON 的 name，保持一致。"""
    body = request.json or {}
    dirname = body.get('dir')
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': '标题不能为空'}), 400
    if len(name) > 100:
        return jsonify({'success': False, 'message': '标题过长'}), 400

    safe_key = re.sub(r'[^a-zA-Z0-9_]', '', key)
    sheet_path = _wh_sheet_path(key, dirname)
    if not os.path.isfile(sheet_path):
        return jsonify({'success': False, 'message': f'sheet "{key}" 不存在'}), 404

    with open(sheet_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    data['name'] = name
    with open(sheet_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 同步 _index.json 里的 name
    index_path = _wh_index_path(dirname)
    if os.path.isfile(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)
        changed = False
        for entry in index:
            if entry.get('key') == safe_key:
                entry['name'] = name
                changed = True
        if changed:
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(index, f, ensure_ascii=False, indent=2)

    _load_all_postcode_maps.cache_clear()
    _logger.info('warehouse sheet rename key=%s dir=%s name=%s', key, dirname, name)
    return jsonify({'success': True, 'name': name})


@api_bp.route('/warehouse-sheet/<key>/copy', methods=['POST'])
@admin_required
def warehouse_sheet_copy(key):
    """复制海外仓价格表（仅后台副本，不影响前台展示）。
    新表 key = {key}_copy，若已存在则依次尝试 _copy2 / _copy3…"""
    body = request.json or {}
    dirname = body.get('dir') or 'warehouse_au_dahuo'

    sheet_path = _wh_sheet_path(key, dirname)
    if not os.path.isfile(sheet_path):
        return jsonify({'success': False, 'message': f'sheet "{key}" 不存在'}), 404

    index_path = _wh_index_path(dirname)
    if not os.path.isfile(index_path):
        return jsonify({'success': False, 'message': '目录文件不存在'}), 404

    with open(sheet_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    with open(index_path, 'r', encoding='utf-8') as f:
        index = json.load(f)

    existing_keys = {e['key'] for e in index}
    # 找一个未占用的新 key
    candidate = key + '_copy'
    suffix = 2
    while candidate in existing_keys:
        candidate = f'{key}_copy{suffix}'
        suffix += 1

    new_name = data.get('name', key) + ' 副本'
    if suffix > 2:
        new_name = data.get('name', key) + f' 副本{suffix - 1}'

    new_data = dict(data)
    new_data['key'] = candidate
    new_data['name'] = new_name
    # 副本不带搜索缓存字段（重建时会重新生成）
    new_data.pop('postcode_zone_map', None)

    new_path = _wh_sheet_path(candidate, dirname)
    with open(new_path, 'w', encoding='utf-8') as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)

    # 统计行数（与 _index.json 保持一致的格式）
    row_count = sum(len(sec.get('rows') or []) for sec in new_data.get('sections') or [])
    index.append({'key': candidate, 'name': new_name, 'row_count': row_count,
                  'is_large': data.get('is_large', False)})
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    _load_all_postcode_maps.cache_clear()
    _logger.info('warehouse sheet copy src=%s new=%s dir=%s', key, candidate, dirname)
    return jsonify({'success': True, 'key': candidate, 'name': new_name})


# 原始4张表不允许删除
_WH_PROTECTED_KEYS = {'allied', 'border', 'tfm', 'toll'}


@api_bp.route('/warehouse-sheet/<key>/delete', methods=['POST'])
@admin_required
def warehouse_sheet_delete(key):
    """删除副本价格表。原始4张表（allied/border/tfm/toll）受保护，不可删除。"""
    if key in _WH_PROTECTED_KEYS:
        return jsonify({'success': False, 'message': '原始价格表不可删除'}), 403

    body = request.json or {}
    dirname = body.get('dir') or 'warehouse_au_dahuo'

    sheet_path = _wh_sheet_path(key, dirname)
    if not os.path.isfile(sheet_path):
        return jsonify({'success': False, 'message': f'sheet "{key}" 不存在'}), 404

    index_path = _wh_index_path(dirname)
    if not os.path.isfile(index_path):
        return jsonify({'success': False, 'message': '目录文件不存在'}), 404

    os.remove(sheet_path)

    with open(index_path, 'r', encoding='utf-8') as f:
        index = json.load(f)
    index = [e for e in index if e.get('key') != key]
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    _load_all_postcode_maps.cache_clear()
    _logger.info('warehouse sheet delete key=%s dir=%s', key, dirname)
    return jsonify({'success': True})


@api_bp.route('/warehouse-sheet/<key>/save', methods=['POST'])
@admin_required
def warehouse_sheet_save(key):
    """保存小表编辑数据"""
    body = request.json or {}
    path = _wh_sheet_path(key, body.get('dir'))
    if not os.path.isfile(path):
        return jsonify({'success': False, 'message': f'sheet "{key}" 不存在'}), 404

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    sections = body.get('sections')
    if not isinstance(sections, list):
        return jsonify({'success': False, 'message': '参数错误'}), 400

    orig_sections = data.get('sections', [])
    new_sections = []
    for sec_data in sections:
        oidx = sec_data.get('_oidx')
        if isinstance(oidx, int) and 0 <= oidx < len(orig_sections):
            base = dict(orig_sections[oidx])
        else:
            base = {}
        if sec_data.get('type') == 'richtext':
            base['type'] = 'richtext'
            base['html'] = sec_data.get('html', '')
        if 'rows' in sec_data:
            base['rows'] = sec_data['rows']
        if 'title' in sec_data:
            base['title'] = sec_data['title']
        # #50 快递副本（warehouse_au_dahuo）允许编辑表头；其余目录不发 headers，沿用原文件
        if isinstance(sec_data.get('headers'), list):
            base['headers'] = sec_data['headers']
        # 每列的计价公式选择（''=手动填写；'15'/'30'/'100'/'500'=按对应公式自动算价）
        if isinstance(sec_data.get('col_formulas'), list):
            base['col_formulas'] = sec_data['col_formulas']
        new_sections.append(base)
    data['sections'] = new_sections

    # 价格表下方的备注（免责声明），单条，留空则前台用默认文案
    if 'result_note' in body:
        data['result_note'] = (body.get('result_note') or '').strip()
        # 清理旧的空运/海运双备注字段，避免残留
        data.pop('result_note_air', None)
        data.pop('result_note_sea', None)

    # 月度参数面板下方的富文本备注（支持加粗/改色）
    if 'panel_note_html' in body:
        data['panel_note_html'] = body.get('panel_note_html') or ''

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    _load_all_postcode_maps.cache_clear()
    _logger.info('warehouse sheet save key=%s', key)
    return jsonify({'success': True})


# 海外仓运费总价参数：GST 固定 10%；燃油率**按表(key)独立**存于 _settings.json。
# 结构：{"gst_rate":10, "fuel_rates":{"allied":20,"border":20,"tfm":20,"toll":20}}
# 兼容旧结构 {"fuel_rate":20,...}：作为各表默认。
_WH_SHEET_KEYS = ['allied', 'border', 'tfm', 'toll']


def _wh_settings_path(dirname=None):
    return os.path.join(_wh_dir(dirname), '_settings.json')


def _read_wh_settings(dirname=None):
    """海外仓运费参数。

    存储结构（data/<dir>/_settings.json）：
        {
          "gst_rate": 10,
          "monthly": {
            "allied": {"8": {"unit_price":36,"sea_unit_price":5,"exchange_rate":4.8,"fuel_rate":12.3}, ...},
            "border": {...}, "tfm": {...}, "toll": {...}
          }
        }
    返回时附带按“服务器当前月”解析出的各表 fuel_rates / exchange_rates（前台会用客户端当前月
    再从 monthly 精确取值；这里的解析值仅作后备与旧读者兼容）。
    兼容旧结构：顶层 fuel_rate（单值）/ fuel_rates（各表）→ 落入各表各月的 fuel_rate 默认。
    """
    path = _wh_settings_path(dirname)
    gst_rate = 10
    fuel_default = 20
    legacy_fuel_rates = {}
    monthly = {}
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                if 'gst_rate' in saved:
                    gst_rate = saved['gst_rate']
                if 'fuel_rate' in saved:          # 旧单值
                    fuel_default = saved['fuel_rate']
                fr = saved.get('fuel_rates')      # 旧各表
                if isinstance(fr, dict):
                    legacy_fuel_rates = dict(fr)
                mo = saved.get('monthly')
                if isinstance(mo, dict):
                    for k, v in mo.items():
                        if isinstance(v, dict):
                            monthly[k] = {str(mk): dict(mv) for mk, mv in v.items() if isinstance(mv, dict)}
        except (ValueError, OSError):
            pass
    # 补齐 4 张表的 monthly 容器
    for k in _WH_SHEET_KEYS:
        monthly.setdefault(k, {})

    cur_month = str(datetime.now().month)

    def _month_param(k, field, default):
        rec = monthly.get(k, {}).get(cur_month) or {}
        v = rec.get(field)
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    fuel_rates = {}
    exchange_rates = {}
    for k in _WH_SHEET_KEYS:
        fuel_rates[k] = _month_param(k, 'fuel_rate', legacy_fuel_rates.get(k, fuel_default))
        exchange_rates[k] = _month_param(k, 'exchange_rate', 0)
    return {
        'gst_rate': gst_rate,
        'monthly': monthly,
        'current_month': int(cur_month),
        'fuel_rates': fuel_rates,        # 当前月解析（后备/兼容）
        'exchange_rates': exchange_rates,
    }


@api_bp.route('/warehouse-settings')
def warehouse_settings_get():
    """海外仓运费总价参数（各表分月：头程单价/汇率/燃油率%，GST%），前台公开读取。"""
    return jsonify({'success': True, 'data': _read_wh_settings(request.args.get('dir'))})


@api_bp.route('/warehouse-settings/save', methods=['POST'])
@admin_required
def warehouse_settings_save():
    body = request.json or {}
    dirname = body.get('dir')

    def _num(v, default):
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    # 读磁盘原始结构（保留其它表/其它月），只改本次提交部分
    path = _wh_settings_path(dirname)
    raw = {}
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                raw = json.load(f) or {}
        except (ValueError, OSError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    monthly = raw.get('monthly')
    if not isinstance(monthly, dict):
        monthly = {}
    # 旧 fuel_rates 一次性迁移进 monthly 当前月，避免升级后燃油率丢失
    legacy_fr = raw.get('fuel_rates') if isinstance(raw.get('fuel_rates'), dict) else {}
    cur_month = str(datetime.now().month)
    for k in _WH_SHEET_KEYS:
        monthly.setdefault(k, {})
        if k in legacy_fr and cur_month not in monthly[k]:
            monthly[k][cur_month] = {'fuel_rate': _num(legacy_fr[k], 20)}

    gst_rate = _num(body.get('gst_rate'), raw.get('gst_rate', 10))
    key = body.get('key')
    valid_keys = _wh_index_keys(dirname)

    # 分月参数保存：{key, month, unit_price, sea_unit_price, tail_per, tail_op, exchange_rate, fuel_rate}
    if key in valid_keys and body.get('month') is not None:
        month = str(int(_num(body.get('month'), datetime.now().month)))
        rec = dict(monthly.get(key, {}).get(month) or {})
        for field in ('unit_price', 'hk_unit_price', 'sea_unit_price', 'tail_per', 'tail_op', 'exchange_rate', 'fuel_rate'):
            if field in body:
                rec[field] = _num(body.get(field), rec.get(field, 0))
        monthly.setdefault(key, {})[month] = rec
    # 兼容旧调用：{key, fuel_rate}（写入当前月燃油率）
    elif key in valid_keys and 'fuel_rate' in body:
        month = cur_month
        rec = dict(monthly.get(key, {}).get(month) or {})
        rec['fuel_rate'] = _num(body.get('fuel_rate'), rec.get('fuel_rate', 20))
        monthly.setdefault(key, {})[month] = rec

    data = {'gst_rate': gst_rate, 'monthly': monthly}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _logger.info('warehouse settings save dir=%s key=%s month=%s', dirname, key, body.get('month'))
    return jsonify({'success': True, 'data': _read_wh_settings(dirname)})




_XIAOBAO_DATA_DIR = os.path.join(config._BASE_DIR, 'data', 'xiaobao')


def _xb_index_path():
    return os.path.join(_XIAOBAO_DATA_DIR, '_index.json')


def _xb_sheet_path(key):
    safe = re.sub(r'[^a-zA-Z0-9_]', '', key)
    return os.path.join(_XIAOBAO_DATA_DIR, f'{safe}.json')


@api_bp.route('/xiaobao-sheets')
def xiaobao_sheets_index():
    path = _xb_index_path()
    if not os.path.isfile(path):
        return jsonify({'success': False, 'message': '数据未初始化'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        index = json.load(f)
    return jsonify({'success': True, 'data': index})


@api_bp.route('/xiaobao-sheet/<key>')
def xiaobao_sheet_detail(key):
    path = _xb_sheet_path(key)
    if not os.path.isfile(path):
        return jsonify({'success': False, 'message': f'sheet "{key}" 不存在'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return jsonify({'success': True, 'data': data})


@api_bp.route('/xiaobao-sheet/<key>/save', methods=['POST'])
@admin_required
def xiaobao_sheet_save(key):
    """保存价格表编辑数据"""
    path = _xb_sheet_path(key)
    if not os.path.isfile(path):
        return jsonify({'success': False, 'message': f'sheet "{key}" 不存在'}), 404

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    body = request.json or {}
    sections = body.get('sections')
    if not isinstance(sections, list):
        return jsonify({'success': False, 'message': '参数错误'}), 400

    orig_sections = data.get('sections', [])
    new_sections = []
    for sec_data in sections:
        oidx = sec_data.get('_oidx')
        if isinstance(oidx, int) and 0 <= oidx < len(orig_sections):
            base = dict(orig_sections[oidx])
        else:
            base = {}
        if sec_data.get('type') == 'richtext':
            base['type'] = 'richtext'
            base['html'] = sec_data.get('html', '')
        if 'rows' in sec_data:
            base['rows'] = sec_data['rows']
        if 'title' in sec_data:
            base['title'] = sec_data['title']
        new_sections.append(base)
    data['sections'] = new_sections

    # 价格表下方的备注（免责声明），空运/海运各一条，留空则前台用默认文案
    if 'result_note_air' in body:
        data['result_note_air'] = (body.get('result_note_air') or '').strip()
    if 'result_note_sea' in body:
        data['result_note_sea'] = (body.get('result_note_sea') or '').strip()

    # 月度参数面板下方的富文本备注（支持加粗/改色），与 section 的 html 同样处理
    if 'panel_note_html' in body:
        data['panel_note_html'] = body.get('panel_note_html') or ''

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    _logger.info('xiaobao sheet save key=%s', key)
    return jsonify({'success': True})


@api_bp.route('/xiaobao-adjust-price', methods=['POST'])
@admin_required
def xiaobao_adjust_price():
    body = request.json or {}
    sheet_key = body.get('sheet_key', '')
    pct_raw = body.get('percentage')

    if not sheet_key or pct_raw is None:
        return jsonify({'success': False, 'message': '缺少 sheet_key 或 percentage'}), 400

    try:
        pct = float(pct_raw)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'percentage 必须是数字'}), 400

    if abs(pct) > 100:
        return jsonify({'success': False, 'message': '调幅不能超过 ±100%'}), 400

    path = _xb_sheet_path(sheet_key)
    if not os.path.isfile(path):
        return jsonify({'success': False, 'message': f'sheet "{sheet_key}" 不存在'}), 404

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    factor = 1 + pct / 100.0
    adjusted = 0

    for sec in data.get('sections', []):
        if sec.get('type') == 'zone_table':
            continue
        for row in sec.get('rows', []):
            for ci, cell in enumerate(row):
                if cell == '' or cell is None:
                    continue
                try:
                    num = float(cell)
                except (ValueError, TypeError):
                    continue
                new_val = round(num * factor, 2)
                if new_val == int(new_val):
                    new_val = int(new_val)
                row[ci] = new_val
                adjusted += 1

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))

    _logger.info('xiaobao adjust price sheet=%s pct=%s adjusted=%s', sheet_key, pct, adjusted)
    return jsonify({'success': True, 'adjusted_count': adjusted})


def _normalize_postcode(raw):
    """归一化为 4 位数字邮编，非法返回空串"""
    digits = ''.join(c for c in str(raw or '') if c.isdigit())
    if not digits or len(digits) > 4:
        return ''
    return digits.zfill(4)


@api_bp.route('/xiaobao-zone-lookup')
def xiaobao_zone_lookup():
    """多邮编 → 分区查询（读数据库）"""
    raw = request.args.get('codes') or request.args.get('code') or ''
    tokens = re.split(r'[\s,，、;；]+', raw.strip())
    codes = []
    seen = set()
    for t in tokens:
        pc = _normalize_postcode(t)
        if pc and pc not in seen:
            seen.add(pc)
            codes.append(pc)
        if len(codes) >= 50:
            break

    if not codes:
        return jsonify({'success': False, 'message': '请输入邮编'}), 400

    with db.get_db() as conn:
        cursor = conn.cursor()
        placeholders = ','.join('?' * len(codes))
        cursor.execute(
            f'SELECT postcode, zone, suburb, state FROM xiaobao_zones WHERE postcode IN ({placeholders})',
            codes,
        )
        by_code = {}
        for r in cursor.fetchall():
            by_code.setdefault(r['postcode'], {
                'zone': r['zone'], 'suburb': r['suburb'], 'state': r['state']
            })

    data = []
    for pc in codes:
        hit = by_code.get(pc)
        if hit:
            data.append({'code': pc, 'found': True, 'zone': hit['zone'],
                         'suburb': hit['suburb'], 'state': hit['state']})
        else:
            data.append({'code': pc, 'found': False})

    return jsonify({'success': True, 'data': data})


@api_bp.route('/xiaobao-zones')
@admin_required
def xiaobao_zones_list():
    """分区表分页查询（后台维护用）"""
    search = (request.args.get('search') or '').strip()
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(200, max(10, request.args.get('per_page', 50, type=int)))
    offset = (page - 1) * per_page

    with db.get_db() as conn:
        cursor = conn.cursor()
        where = ''
        params = []
        if search:
            where = 'WHERE postcode LIKE ? OR zone LIKE ? OR suburb LIKE ? OR state LIKE ?'
            like = f'%{search}%'
            params = [like, like, like, like]
        cursor.execute(f'SELECT COUNT(*) FROM xiaobao_zones {where}', params)
        total = cursor.fetchone()[0]
        total_pages = max(1, (total + per_page - 1) // per_page)
        cursor.execute(
            f'SELECT id, postcode, zone, suburb, state FROM xiaobao_zones {where} '
            'ORDER BY postcode, id LIMIT ? OFFSET ?',
            params + [per_page, offset],
        )
        rows = [dict(r) for r in cursor.fetchall()]

    return jsonify({'success': True, 'data': rows, 'total': total,
                    'page': page, 'total_pages': total_pages, 'per_page': per_page})


@api_bp.route('/xiaobao-zones/add', methods=['POST'])
@admin_required
def xiaobao_zones_add():
    body = request.json or {}
    postcode = _normalize_postcode(body.get('postcode'))
    zone = (body.get('zone') or '').strip()
    if not postcode or not zone:
        return jsonify({'success': False, 'message': '邮编（4位）和分区必填'}), 400
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO xiaobao_zones (postcode, zone, suburb, state) VALUES (?, ?, ?, ?)',
            (postcode, zone, (body.get('suburb') or '').strip(), (body.get('state') or '').strip()),
        )
        conn.commit()
        return jsonify({'success': True, 'id': cursor.lastrowid})


@api_bp.route('/xiaobao-zones/<int:zid>/update', methods=['POST'])
@admin_required
def xiaobao_zones_update(zid):
    body = request.json or {}
    fields = []
    params = []
    if 'postcode' in body:
        pc = _normalize_postcode(body.get('postcode'))
        if not pc:
            return jsonify({'success': False, 'message': '邮编必须是4位数字'}), 400
        fields.append('postcode = ?')
        params.append(pc)
    if 'zone' in body:
        zone = (body.get('zone') or '').strip()
        if not zone:
            return jsonify({'success': False, 'message': '分区不能为空'}), 400
        fields.append('zone = ?')
        params.append(zone)
    if 'suburb' in body:
        fields.append('suburb = ?')
        params.append((body.get('suburb') or '').strip())
    if 'state' in body:
        fields.append('state = ?')
        params.append((body.get('state') or '').strip())
    if not fields:
        return jsonify({'success': False, 'message': '无更新字段'}), 400
    params.append(zid)
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f'UPDATE xiaobao_zones SET {", ".join(fields)} WHERE id = ?', params)
        conn.commit()
        return jsonify({'success': True})


@api_bp.route('/xiaobao-zones/<int:zid>/delete', methods=['POST'])
@admin_required
def xiaobao_zones_delete(zid):
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM xiaobao_zones WHERE id = ?', (zid,))
        conn.commit()
        return jsonify({'success': True})


@api_bp.route('/xiaobao-zones/bulk-import', methods=['POST'])
@admin_required
def xiaobao_zones_bulk_import():
    """文本批量导入：每行 postcode, zone[, suburb[, state]]（逗号或制表符分隔）"""
    body = request.json or {}
    text = body.get('text') or ''
    mode = body.get('mode') or 'append'
    if mode not in ('append', 'replace'):
        mode = 'append'

    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r'[\t,，]+', line)
        parts = [p.strip() for p in parts]
        pc = _normalize_postcode(parts[0]) if parts else ''
        zone = parts[1].strip() if len(parts) > 1 else ''
        if not pc or not zone:
            continue
        suburb = parts[2] if len(parts) > 2 else ''
        state = parts[3] if len(parts) > 3 else ''
        rows.append((pc, zone, suburb, state))

    with db.get_db() as conn:
        cursor = conn.cursor()
        if mode == 'replace':
            cursor.execute('DELETE FROM xiaobao_zones')
        cursor.executemany(
            'INSERT INTO xiaobao_zones (postcode, zone, suburb, state) VALUES (?, ?, ?, ?)',
            rows,
        )
        conn.commit()

    _logger.info('xiaobao zones bulk import mode=%s inserted=%s', mode, len(rows))
    return jsonify({'success': True, 'inserted': len(rows)})


@api_bp.route('/xiaobao-settings')
def xiaobao_settings_get():
    """月度参数（单价/汇率/燃油费率），前台公开读取"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT month, unit_price, exchange_rate, fuel_rate, sea_unit_price FROM xiaobao_month_settings ORDER BY month')
        data = {}
        for r in cursor.fetchall():
            data[str(r['month'])] = {
                'unit_price': r['unit_price'],
                'exchange_rate': r['exchange_rate'],
                'fuel_rate': r['fuel_rate'],
                'sea_unit_price': r['sea_unit_price'],
            }
    return jsonify({'success': True, 'data': data})


@api_bp.route('/xiaobao-settings/save', methods=['POST'])
@admin_required
def xiaobao_settings_save():
    """批量保存 12 个月参数"""
    body = request.json or {}
    settings = body.get('settings')
    if not isinstance(settings, list):
        return jsonify({'success': False, 'message': '参数错误'}), 400

    def _num(v):
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    saved = 0
    with db.get_db() as conn:
        cursor = conn.cursor()
        for item in settings:
            try:
                month = int(item.get('month'))
            except (ValueError, TypeError):
                continue
            if month < 1 or month > 12:
                continue
            cursor.execute(
                'INSERT INTO xiaobao_month_settings (month, unit_price, exchange_rate, fuel_rate, sea_unit_price) '
                'VALUES (?, ?, ?, ?, ?) '
                'ON CONFLICT(month) DO UPDATE SET unit_price=excluded.unit_price, '
                'exchange_rate=excluded.exchange_rate, fuel_rate=excluded.fuel_rate, '
                'sea_unit_price=excluded.sea_unit_price',
                (month, _num(item.get('unit_price')), _num(item.get('exchange_rate')),
                 _num(item.get('fuel_rate')), _num(item.get('sea_unit_price'))),
            )
            saved += 1
        conn.commit()

    _logger.info('xiaobao settings save count=%s', saved)
    return jsonify({'success': True, 'saved': saved})
