# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, redirect, url_for, abort

import config
import db
import models
from util import admin_required

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
            article_code = models.generate_article_code()
            cursor.execute('INSERT INTO articles (title, category_id, article_code) VALUES (?, ?, ?)',
                           (title, category_id, article_code))
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
            cursor.execute('UPDATE articles SET title = ?, category_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                           (title, category_id, article_id))
            conn.commit()
        cursor.execute('SELECT * FROM articles WHERE id = ?', (article_id,))
        article = cursor.fetchone()
        if not article:
            abort(404)
        article = dict(article)
        categories = models.get_all_categories()
        cursor.execute('SELECT * FROM modules WHERE article_id = ? ORDER BY sort_order', (article_id,))
        modules = [dict(r) for r in cursor.fetchall()]
        return render_template('admin/editor.html', article=article, categories=categories, modules=modules,
                              back_url=url_for('admin.articles'), fixed_category_id=None, fixed_category_name=None)


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
            cursor.execute('INSERT INTO articles (title, category_id, article_code) VALUES (?, ?, ?)',
                           (article['title'] + ' (副本)', article['category_id'], new_code))
            new_article_id = cursor.lastrowid
            cursor.execute('SELECT * FROM modules WHERE article_id = ? ORDER BY sort_order', (article_id,))
            modules = cursor.fetchall()
            for module in modules:
                m = dict(module)
                cursor.execute('INSERT INTO modules (article_id, type, content, sort_order) VALUES (?, ?, ?, ?)',
                               (new_article_id, m['type'], m['content'], m['sort_order']))
            conn.commit()
        return redirect(url_for('admin.articles'))


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
