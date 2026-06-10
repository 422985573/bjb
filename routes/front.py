# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, session

import config
import db
import models

front_bp = Blueprint('front', __name__)


@front_bp.route('/')
def index():
    """首页（分页）；侧栏分类按排序；默认展示排序第一的分类下的文章；?all=1 查看全部"""
    show_all = request.args.get('all', type=int) == 1
    all_categories = models.get_all_categories()
    first_category_id = all_categories[0]['id'] if all_categories else None

    show_all_articles = show_all or first_category_id is None
    use_index_route_for_pages = bool(not show_all_articles)
    current_category = first_category_id if not show_all_articles else None

    with db.get_db() as conn:
        cursor = conn.cursor()
        if show_all_articles:
            cursor.execute('SELECT COUNT(*) FROM articles WHERE is_published = 1')
            total = cursor.fetchone()[0]
            total_pages = max(1, (total + config.PER_PAGE - 1) // config.PER_PAGE)
            page = max(1, min(request.args.get('page', 1, type=int), total_pages))
            offset = (page - 1) * config.PER_PAGE
            cursor.execute('''
                SELECT a.*, c.name as category_name FROM articles a
                LEFT JOIN categories c ON a.category_id = c.id
                WHERE a.is_published = 1
                ORDER BY a.created_at DESC LIMIT ? OFFSET ?
            ''', (config.PER_PAGE, offset))
        else:
            cursor.execute(
                'SELECT COUNT(*) FROM articles WHERE category_id = ? AND is_published = 1',
                (first_category_id,),
            )
            total = cursor.fetchone()[0]
            total_pages = max(1, (total + config.PER_PAGE - 1) // config.PER_PAGE)
            page = max(1, min(request.args.get('page', 1, type=int), total_pages))
            offset = (page - 1) * config.PER_PAGE
            cursor.execute('''
                SELECT a.*, c.name as category_name FROM articles a
                LEFT JOIN categories c ON a.category_id = c.id
                WHERE a.category_id = ? AND a.is_published = 1
                ORDER BY a.created_at DESC LIMIT ? OFFSET ?
            ''', (first_category_id, config.PER_PAGE, offset))

        articles = cursor.fetchall()
        return render_template(
            'index.html',
            categories=all_categories,
            articles=articles,
            page=page,
            total_pages=total_pages,
            total=total,
            per_page=config.PER_PAGE,
            show_all_articles=show_all_articles,
            current_category=current_category,
            use_index_route_for_pages=use_index_route_for_pages,
        )


@front_bp.route('/category/<int:category_id>')
def category_articles(category_id):
    """分类文章列表（分页）"""
    categories = models.get_all_categories()
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM articles WHERE category_id = ? AND is_published = 1', (category_id,))
        total = cursor.fetchone()[0]
        total_pages = max(1, (total + config.PER_PAGE - 1) // config.PER_PAGE)
        page = max(1, min(request.args.get('page', 1, type=int), total_pages))
        offset = (page - 1) * config.PER_PAGE
        cursor.execute('''
            SELECT a.*, c.name as category_name FROM articles a
            LEFT JOIN categories c ON a.category_id = c.id
            WHERE a.category_id = ? AND a.is_published = 1
            ORDER BY a.created_at DESC LIMIT ? OFFSET ?
        ''', (category_id, config.PER_PAGE, offset))
        articles = cursor.fetchall()
        return render_template(
            'index.html',
            categories=categories,
            articles=articles,
            current_category=category_id,
            page=page,
            total_pages=total_pages,
            total=total,
            per_page=config.PER_PAGE,
            show_all_articles=False,
            use_index_route_for_pages=False,
        )


@front_bp.route('/article/<article_code>')
def article_detail(article_code):
    """文章详情页"""
    is_admin = bool(session.get('admin_logged_in'))
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT a.*, c.name as category_name
            FROM articles a
            LEFT JOIN categories c ON a.category_id = c.id
            WHERE a.article_code = ? AND (a.is_published = 1 OR ? = 1)
        ''', (article_code, 1 if is_admin else 0))
        row = cursor.fetchone()
        if not row:
            return '文章不存在', 404
        article = dict(row)
        cursor.execute(
            "SELECT * FROM modules WHERE article_id = ? ORDER BY sort_order",
            (article['id'],)
        )
        modules = [dict(r) for r in cursor.fetchall()]
        mod_types = [(m.get('type') or '').strip().lower() for m in modules]
        has_dg_grid = 'dg_grid' in mod_types
        if 'warehouse_sheets' in mod_types:
            return render_template(
                'article_warehouse.html',
                article=article,
                modules=modules,
            )
        # 仅含 dg_grid（柜类报价表）模块的文章使用独立页模板（无渠道目录、无邮编搜索条）
        article_dg_page = bool(modules) and all(t == 'dg_grid' for t in mod_types)
        if article_dg_page:
            return render_template(
                'article_dg.html',
                article=article,
                modules=modules,
                has_dg_grid=True,
            )
        return render_template(
            'article.html',
            article=article,
            modules=modules,
            has_dg_grid=has_dg_grid,
        )
