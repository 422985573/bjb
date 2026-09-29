# -*- coding: utf-8 -*-
import json
import os
import re

from flask import Blueprint, render_template, request, redirect, url_for, abort

import config
import db
import models
from util import admin_required, atomic_write_json, data_dir_lock

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """管理员登录"""
    if request.method == 'POST':
        password = request.form.get('password')
        if password == config.ADMIN_PASSWORD:
            from flask import session
            session['admin_logged_in'] = True
            return redirect(url_for('admin.articles'))
        return render_template('admin/login.html', error='密码错误')
    return render_template('admin/login.html')


@admin_bp.route('/logout')
def logout():
    """管理员登出"""
    from flask import session
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin.login'))


@admin_bp.route('/')
@admin_bp.route('/articles')
@admin_required
def articles():
    """文章管理列表"""
    category_filter = request.args.get('category_id', type=int)
    categories = models.get_all_categories()
    page = max(1, request.args.get('page', 1, type=int))
    with db.get_db() as conn:
        cursor = conn.cursor()
        if category_filter:
            cursor.execute('SELECT COUNT(*) FROM articles WHERE category_id = ?', (category_filter,))
            total = cursor.fetchone()[0]
            total_pages = max(1, (total + config.PER_PAGE - 1) // config.PER_PAGE)
            page = min(page, total_pages)
            offset = (page - 1) * config.PER_PAGE
            cursor.execute('''
                SELECT a.*, c.name as category_name FROM articles a
                LEFT JOIN categories c ON a.category_id = c.id
                WHERE a.category_id = ? ORDER BY a.created_at DESC LIMIT ? OFFSET ?
            ''', (category_filter, config.PER_PAGE, offset))
            articles_list = [dict(r) for r in cursor.fetchall()]
        else:
            cursor.execute('SELECT COUNT(*) FROM articles')
            total = cursor.fetchone()[0]
            total_pages = max(1, (total + config.PER_PAGE - 1) // config.PER_PAGE)
            page = min(page, total_pages)
            offset = (page - 1) * config.PER_PAGE
            cursor.execute('''
                SELECT a.*, c.name as category_name FROM articles a
                LEFT JOIN categories c ON a.category_id = c.id
                ORDER BY a.created_at DESC LIMIT ? OFFSET ?
            ''', (config.PER_PAGE, offset))
            articles_list = [dict(r) for r in cursor.fetchall()]
        return render_template('admin/articles.html', categories=categories, articles=articles_list,
                              category_filter=category_filter, page=page, total_pages=total_pages,
                              total=total, per_page=config.PER_PAGE)


@admin_bp.route('/article/create', methods=['GET', 'POST'])
@admin_required
def article_create():
    """创建文章"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        if request.method == 'POST':
            title = request.form.get('title')
            category_id = request.form.get('category_id')
            is_published = 1 if request.form.get('is_published', '1') == '1' else 0
            article_code = models.generate_article_code()
            cursor.execute(
                'INSERT INTO articles (title, category_id, article_code, is_published) VALUES (?, ?, ?, ?)',
                (title, category_id, article_code, is_published)
            )
            article_id = cursor.lastrowid
            conn.commit()
            return redirect(url_for('admin.article_edit', article_id=article_id))
        categories = models.get_all_categories()
        return render_template('admin/article_form.html', categories=categories, article=None,
                              fixed_category_id=None, channel_name=None)


@admin_bp.route('/article/<int:article_id>/edit', methods=['GET', 'POST'])
@admin_required
def article_edit(article_id):
    """编辑文章"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        if request.method == 'POST':
            title = request.form.get('title')
            category_id = request.form.get('category_id')
            is_published = 1 if request.form.get('is_published', '1') == '1' else 0
            cursor.execute(
                'UPDATE articles SET title = ?, category_id = ?, is_published = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (title, category_id, is_published, article_id)
            )
            conn.commit()
        cursor.execute('SELECT * FROM articles WHERE id = ?', (article_id,))
        article = cursor.fetchone()
        if not article:
            abort(404)
        article = dict(article)
        categories = models.get_all_categories()
        cursor.execute(
            "SELECT * FROM modules WHERE article_id = ? ORDER BY sort_order",
            (article_id,)
        )
        modules = [dict(r) for r in cursor.fetchall()]
        mod_types = [m.get('type', '') for m in modules]
        if 'warehouse_sheets' in mod_types:
            return redirect(url_for('admin.warehouse_editor', article_id=article_id))
        if 'xiaobao_sheets' in mod_types:
            return redirect(url_for('admin.xiaobao_editor', article_id=article_id))
        # DG：PS/海关查验说明迁出表格并入备注；GET 时若规范化结果变化则写回库（含 Excel 栅格非 v2）
        from services.dg_quote_grid import (
            normalize_dg_v2_ps_rows_to_remark,
            normalize_excel_grid_content_storage,
        )

        _dg_dirty = False
        for m in modules:
            if m.get('type') != 'dg_grid':
                continue
            raw = m.get('content')
            if not raw:
                continue
            try:
                c = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(c, dict):
                continue
            sch = int(c.get('schema') or 0)
            if sch == 2:
                new_c = normalize_dg_v2_ps_rows_to_remark(dict(c))
            else:
                new_c = normalize_excel_grid_content_storage(dict(c))
            old_s = json.dumps(c, ensure_ascii=False, sort_keys=True)
            new_s = json.dumps(new_c, ensure_ascii=False, sort_keys=True)
            if new_s != old_s:
                blob = json.dumps(new_c, ensure_ascii=False)
                cursor.execute('UPDATE modules SET content = ? WHERE id = ?', (blob, m['id']))
                m['content'] = blob
                _dg_dirty = True
        if _dg_dirty:
            conn.commit()
        # #50「新大货快递报价文章」：在编辑器提供 4 张海外仓表（独立副本）的编辑入口。
        dahuo_wh_sheets = []
        cat_name = next((c['name'] for c in categories if c['id'] == article.get('category_id')), '')
        if cat_name == '新大货快递报价文章':
            dahuo_index = os.path.join(config._BASE_DIR, 'data', 'warehouse_au_dahuo', '_index.json')
            if os.path.isfile(dahuo_index):
                from routes.api import _wh_reconcile_index, _read_wh_hidden
                with open(dahuo_index, 'r', encoding='utf-8') as f:
                    _idx = json.load(f)
                # 补录磁盘上有文件却漏收录的孤儿副本，保证后台侧栏与磁盘一致
                _idx = _wh_reconcile_index(_idx, 'warehouse_au_dahuo')
                _hidden = _read_wh_hidden('warehouse_au_dahuo')
                dahuo_wh_sheets = [
                    {'key': s['key'], 'name': s['name'], 'hidden': s['key'] in _hidden}
                    for s in _idx if s.get('key') != 'mulu'
                ]
        return render_template('admin/editor.html', article=article, categories=categories, modules=modules,
                              back_url=url_for('admin.articles'), fixed_category_id=None, fixed_category_name=None,
                              dahuo_wh_sheets=dahuo_wh_sheets, dahuo_wh_dir='warehouse_au_dahuo')


@admin_bp.route('/article/<int:article_id>/copy')
@admin_required
def article_copy(article_id):
    """复制文章"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM articles WHERE id = ?', (article_id,))
        row = cursor.fetchone()
        article = dict(row) if row else None
        if article:
            new_code = models.generate_article_code()
            cursor.execute(
                'INSERT INTO articles (title, category_id, article_code, is_published) VALUES (?, ?, ?, ?)',
                (article['title'] + ' (副本)', article['category_id'], new_code, 0)
            )
            new_article_id = cursor.lastrowid
            cursor.execute(
                "SELECT * FROM modules WHERE article_id = ? ORDER BY sort_order",
                (article_id,)
            )
            modules = cursor.fetchall()
            new_xiaobao_module_id = None
            for module in modules:
                m = dict(module)
                cursor.execute('INSERT INTO modules (article_id, type, content, sort_order) VALUES (?, ?, ?, ?)',
                               (new_article_id, m['type'], m['content'], m['sort_order']))
                if (m.get('type') or '') == 'xiaobao_sheets':
                    new_xiaobao_module_id = cursor.lastrowid
            conn.commit()
            # 小包文章：为副本生成一份独立的价格表 + 参数，避免与原文联动
            if new_xiaobao_module_id is not None:
                _xiaobao_copy_data(conn, article_id, new_article_id,
                                   new_xiaobao_module_id, article['title'] + ' (副本)')
        return redirect(url_for('admin.articles'))


def _xiaobao_copy_data(conn, src_article_id, new_article_id, new_module_id, new_title):
    """复制小包文章时，给副本生成独立数据：
    1) 复制源 sheet JSON 为新 key 文件；2) 追加 _index.json 条目；
    3) 把新模块 content 的 sheet_key 指向新文件（并回填源模块缺失的 sheet_key）；
    4) 复制参数行（xiaobao_article_settings）到新 article_id。
    """
    cursor = conn.cursor()
    src_key = _xiaobao_article_sheet_key(conn, src_article_id)
    if not src_key:
        return
    # 回填源文章模块的 sheet_key（老库单例 id 52 原本无 sheet_key），避免多表后指向歧义
    cursor.execute(
        "SELECT id, content FROM modules WHERE article_id = ? AND type = 'xiaobao_sheets' LIMIT 1",
        (src_article_id,),
    )
    src_mod = cursor.fetchone()
    if src_mod:
        try:
            src_c = json.loads(src_mod['content']) if src_mod['content'] else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            src_c = {}
        if not isinstance(src_c, dict):
            src_c = {}
        if not src_c.get('sheet_key'):
            src_c['sheet_key'] = src_key
            cursor.execute('UPDATE modules SET content = ? WHERE id = ?',
                           (json.dumps(src_c, ensure_ascii=False), src_mod['id']))

    new_key = f'eparcel_{new_article_id}'
    base = os.path.join(config._BASE_DIR, 'data', 'xiaobao')
    src_safe = re.sub(r'[^a-zA-Z0-9_]', '', src_key)
    new_safe = re.sub(r'[^a-zA-Z0-9_]', '', new_key)
    src_path = os.path.join(base, f'{src_safe}.json')
    new_path = os.path.join(base, f'{new_safe}.json')
    index_path = os.path.join(base, '_index.json')

    with data_dir_lock(base):
        if not os.path.isfile(src_path):
            return
        with open(src_path, 'r', encoding='utf-8') as f:
            sheet_data = json.load(f)
        sheet_data['name'] = new_title
        atomic_write_json(new_path, sheet_data, indent=2)

        index = []
        if os.path.isfile(index_path):
            with open(index_path, 'r', encoding='utf-8') as f:
                index = json.load(f)
        src_entry = next((e for e in index if e.get('key') == src_key), None)
        new_entry = dict(src_entry) if src_entry else {}
        new_entry['key'] = new_key
        new_entry['name'] = new_title
        index.append(new_entry)
        atomic_write_json(index_path, index, indent=2)

    # 新模块 content 指向新 sheet_key
    cursor.execute('SELECT content FROM modules WHERE id = ?', (new_module_id,))
    r = cursor.fetchone()
    try:
        new_c = json.loads(r['content']) if r and r['content'] else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        new_c = {}
    if not isinstance(new_c, dict):
        new_c = {}
    new_c['sheet_key'] = new_key
    cursor.execute('UPDATE modules SET content = ? WHERE id = ?',
                   (json.dumps(new_c, ensure_ascii=False), new_module_id))

    # 复制参数行到新 article_id（源无独立行则用旧全局 month=0 兜底）
    cursor.execute(
        'SELECT unit_price, exchange_rate, fuel_rate, sea_unit_price '
        'FROM xiaobao_article_settings WHERE article_id = ?',
        (src_article_id,),
    )
    s = cursor.fetchone()
    if s is None:
        cursor.execute(
            'SELECT unit_price, exchange_rate, fuel_rate, sea_unit_price '
            'FROM xiaobao_month_settings WHERE month = 0'
        )
        s = cursor.fetchone()
    if s is not None:
        cursor.execute(
            'INSERT INTO xiaobao_article_settings '
            '(article_id, unit_price, exchange_rate, fuel_rate, sea_unit_price) '
            'VALUES (?, ?, ?, ?, ?) '
            'ON CONFLICT(article_id) DO UPDATE SET unit_price=excluded.unit_price, '
            'exchange_rate=excluded.exchange_rate, fuel_rate=excluded.fuel_rate, '
            'sea_unit_price=excluded.sea_unit_price',
            (new_article_id, s['unit_price'], s['exchange_rate'], s['fuel_rate'], s['sea_unit_price']),
        )
    conn.commit()


@admin_bp.route('/article/<int:article_id>/delete')
@admin_required
def article_delete(article_id):
    """删除文章"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM modules WHERE article_id = ?', (article_id,))
        cursor.execute('DELETE FROM articles WHERE id = ?', (article_id,))
        conn.commit()
        return redirect(url_for('admin.articles'))


@admin_bp.route('/article/<int:article_id>/warehouse-editor', methods=['GET', 'POST'])
@admin_required
def warehouse_editor(article_id):
    """澳洲海外仓报价编辑器（批量调价）"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        if request.method == 'POST':
            title = request.form.get('title')
            category_id = request.form.get('category_id')
            is_published = 1 if request.form.get('is_published', '1') == '1' else 0
            cursor.execute(
                'UPDATE articles SET title = ?, category_id = ?, is_published = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (title, category_id, is_published, article_id)
            )
            conn.commit()
        cursor.execute('SELECT * FROM articles WHERE id = ?', (article_id,))
        article = cursor.fetchone()
        if not article:
            abort(404)
        article = dict(article)
        categories = models.get_all_categories()
        index_path = os.path.join(config._BASE_DIR, 'data', 'warehouse_au', '_index.json')
        sheets_index = []
        if os.path.isfile(index_path):
            with open(index_path, 'r', encoding='utf-8') as f:
                sheets_index = json.load(f)
        return render_template(
            'admin/warehouse_editor.html',
            article=article,
            categories=categories,
            sheets_index=sheets_index,
            back_url=url_for('admin.articles'),
        )


@admin_bp.route('/warehouse-sheet/<key>/edit')
@admin_required
def warehouse_sheet_edit(key):
    """单个 sheet 编辑页（?dir= 指定数据目录，默认 warehouse_au；#50 用独立副本 warehouse_au_dahuo）"""
    safe_dir = re.sub(r'[^a-zA-Z0-9_]', '', request.args.get('dir') or '') or 'warehouse_au'
    index_path = os.path.join(config._BASE_DIR, 'data', safe_dir, '_index.json')
    if not os.path.isfile(index_path):
        abort(404)
    with open(index_path, 'r', encoding='utf-8') as f:
        sheets_index = json.load(f)
    sheet_meta = next((s for s in sheets_index if s['key'] == key), None)
    if not sheet_meta:
        abort(404)
    # 返回目标：优先用 ?back= 显式传入（仅允许站内相对路径，防开放重定向）
    back = request.args.get('back') or ''
    if not (back.startswith('/') and not back.startswith('//')):
        back = ''
    # embed=1：无边框内嵌模式（供文章编辑器 iframe 同页编辑，隐藏页头/返回，顶部显示可编辑标题）
    is_embed = request.args.get('embed') == '1'
    return render_template(
        'admin/warehouse_sheet_edit.html',
        sheet_key=key,
        sheet_name=sheet_meta['name'],
        is_large=sheet_meta.get('is_large', False),
        data_dir=safe_dir,
        back_url=back,
        is_embed=is_embed,
    )


def _xiaobao_sync_sheet_name(title, sheet_key=None):
    """将文章标题同步为该文章对应虚拟小包报价表 sheet 的 name。

    前台文章页大标题（H1）由 JS 取自 sheet JSON 的 name 字段，因此仅更新
    articles.title 不会改动页面大标题。这里把标题写回 _index.json 中该
    sheet_key 的条目与对应 sheet 文件的 name，保持后台标题与前台大标题一致。
    未传 sheet_key 时回退到 _index.json 第一条（兼容老库单例）。
    """
    base = os.path.join(config._BASE_DIR, 'data', 'xiaobao')
    index_path = os.path.join(base, '_index.json')
    if not os.path.isfile(index_path):
        return
    with data_dir_lock(base):
        with open(index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)
        if not index:
            return
        entry = None
        if sheet_key:
            entry = next((e for e in index if e.get('key') == sheet_key), None)
        if entry is None:
            entry = index[0]
        entry['name'] = title
        atomic_write_json(index_path, index, indent=2)

        safe_key = re.sub(r'[^a-zA-Z0-9_]', '', entry.get('key', ''))
        sheet_path = os.path.join(base, f'{safe_key}.json')
        if safe_key and os.path.isfile(sheet_path):
            with open(sheet_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['name'] = title
            atomic_write_json(sheet_path, data, indent=2)


def _xiaobao_article_sheet_key(conn, article_id):
    """取某篇小包文章对应的 sheet_key：优先读模块 content 里的 sheet_key，
    缺失时回退到 _index.json 的唯一条目（兼容尚未迁移的旧数据）。"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT content FROM modules WHERE article_id = ? AND type = 'xiaobao_sheets' LIMIT 1",
        (article_id,),
    )
    row = cursor.fetchone()
    if row and row['content']:
        try:
            c = json.loads(row['content'])
            if isinstance(c, dict) and c.get('sheet_key'):
                return str(c['sheet_key'])
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    # 回退：_index.json 只有一条时用它（老库单例）
    index_path = os.path.join(config._BASE_DIR, 'data', 'xiaobao', '_index.json')
    if os.path.isfile(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            idx = json.load(f)
        if idx:
            return idx[0].get('key')
    return None


@admin_bp.route('/article/<int:article_id>/xiaobao-editor', methods=['GET', 'POST'])
@admin_required
def xiaobao_editor(article_id):
    """虚拟小包报价表编辑器：直接进入报价表编辑（含文章设置 + 分区表维护入口）"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        if request.method == 'POST':
            title = request.form.get('title')
            category_id = request.form.get('category_id')
            is_published = 1 if request.form.get('is_published', '1') == '1' else 0
            cursor.execute(
                'UPDATE articles SET title = ?, category_id = ?, is_published = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (title, category_id, is_published, article_id)
            )
            conn.commit()
            # 标题同步到本文章报价表 JSON 的 name（前台大标题 H1 取自 sheet.name），保持一致
            if title:
                _xiaobao_sync_sheet_name(title, _xiaobao_article_sheet_key(conn, article_id))
        cursor.execute('SELECT * FROM articles WHERE id = ?', (article_id,))
        article = cursor.fetchone()
        if not article:
            abort(404)
        article = dict(article)
        categories = models.get_all_categories()
        sheet_key = _xiaobao_article_sheet_key(conn, article_id)
        index_path = os.path.join(config._BASE_DIR, 'data', 'xiaobao', '_index.json')
        sheets_index = []
        if os.path.isfile(index_path):
            with open(index_path, 'r', encoding='utf-8') as f:
                sheets_index = json.load(f)
        if not sheets_index or not sheet_key:
            abort(404)
        sheet_meta = next((s for s in sheets_index if s.get('key') == sheet_key), None)
        if sheet_meta is None:
            abort(404)
        return render_template(
            'admin/warehouse_sheet_edit.html',
            sheet_key=sheet_meta['key'],
            sheet_name=sheet_meta['name'],
            is_large=sheet_meta.get('is_large', False),
            api_sheet_base='/api/xiaobao-sheet/',
            article=article,
            categories=categories,
            back_url=url_for('admin.articles'),
        )


@admin_bp.route('/xiaobao-zones')
@admin_required
def xiaobao_zones_page():
    """虚拟小包分区表维护页"""
    return render_template('admin/xiaobao_zones.html')


@admin_bp.route('/channel-postcodes')
@admin_required
def channel_postcodes():
    """渠道拒收邮编编辑页"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT channel, postcodes FROM channel_reject_postcodes ORDER BY channel')
        rows = cursor.fetchall()
        data = {row[0]: (row[1] or '') for row in rows}
        return render_template('admin/channel_postcodes.html', data=data)


@admin_bp.route('/phone-whitelist')
@admin_required
def phone_whitelist():
    """手机号池管理页：可添加/删除手机号（带姓名），用于受限文章访问校验"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, phone, name, created_at FROM phone_whitelist ORDER BY created_at DESC, id DESC')
        phones = [dict(r) for r in cursor.fetchall()]
        return render_template('admin/phone_whitelist.html', phones=phones)
