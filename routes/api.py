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
    """Excel 栅格拆分编辑器：根据 cells/merges 重渲染抬头+费用 div 片段（用于非 table 费用块增删行后刷新）。"""
    from services.dg_quote_grid import render_dg_excel_grid_editable_split_html

    d = request.json or {}
    cells = d.get('cells')
    merges = d.get('merges')
    hr = d.get('header_orange_row')
    if not isinstance(cells, list):
        cells = []
    if not isinstance(merges, list):
        merges = []
    html = render_dg_excel_grid_editable_split_html(cells, merges, hr)
    nrows = len(cells)
    ncols = max((len(r) for r in cells), default=1) if cells else 1
    return jsonify({
        'success': True,
        'html': html,
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

_WAREHOUSE_DATA_DIR = os.path.join(config._BASE_DIR, 'data', 'warehouse_au')


def _wh_index_path():
    return os.path.join(_WAREHOUSE_DATA_DIR, '_index.json')


def _wh_sheet_path(key):
    safe = re.sub(r'[^a-zA-Z0-9_]', '', key)
    return os.path.join(_WAREHOUSE_DATA_DIR, f'{safe}.json')


@api_bp.route('/warehouse-sheets')
def warehouse_sheets_index():
    path = _wh_index_path()
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
    path = _wh_sheet_path(key)
    if not os.path.isfile(path):
        return jsonify({'success': False, 'message': f'sheet "{key}" 不存在'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

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


@api_bp.route('/warehouse-sheet/<key>/save', methods=['POST'])
@admin_required
def warehouse_sheet_save(key):
    """保存小表编辑数据"""
    path = _wh_sheet_path(key)
    if not os.path.isfile(path):
        return jsonify({'success': False, 'message': f'sheet "{key}" 不存在'}), 404

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    body = request.json or {}
    sections = body.get('sections')
    if not isinstance(sections, list):
        return jsonify({'success': False, 'message': '参数错误'}), 400

    for i, sec_data in enumerate(sections):
        if i >= len(data.get('sections', [])):
            break
        if sec_data.get('type') == 'richtext':
            data['sections'][i]['html'] = sec_data.get('html', '')
        if 'rows' in sec_data:
            data['sections'][i]['rows'] = sec_data['rows']
        if 'title' in sec_data:
            data['sections'][i]['title'] = sec_data['title']

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    _load_all_postcode_maps.cache_clear()
    _logger.info('warehouse sheet save key=%s', key)
    return jsonify({'success': True})
