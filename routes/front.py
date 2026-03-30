# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request

import config
import db
import models

front_bp = Blueprint('front', __name__)


@front_bp.route('/')
def index():
    """首页（分页）"""
    categories = models.get_all_categories()
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM articles')
        total = cursor.fetchone()[0]
        total_pages = max(1, (total + config.PER_PAGE - 1) // config.PER_PAGE)
        page = max(1, min(request.args.get('page', 1, type=int), total_pages))
        offset = (page - 1) * config.PER_PAGE
        cursor.execute('''
            SELECT a.*, c.name as category_name FROM articles a
            LEFT JOIN categories c ON a.category_id = c.id
            ORDER BY a.created_at DESC LIMIT ? OFFSET ?
        ''', (config.PER_PAGE, offset))
        articles = cursor.fetchall()
        return render_template('index.html', categories=categories, articles=articles,
                              page=page, total_pages=total_pages, total=total, per_page=config.PER_PAGE)


@front_bp.route('/category/<int:category_id>')
def category_articles(category_id):
    """分类文章列表（分页）"""
    categories = models.get_all_categories()
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM articles WHERE category_id = ?', (category_id,))
        total = cursor.fetchone()[0]
        total_pages = max(1, (total + config.PER_PAGE - 1) // config.PER_PAGE)
        page = max(1, min(request.args.get('page', 1, type=int), total_pages))
        offset = (page - 1) * config.PER_PAGE
        cursor.execute('''
            SELECT a.*, c.name as category_name FROM articles a
            LEFT JOIN categories c ON a.category_id = c.id
            WHERE a.category_id = ? ORDER BY a.created_at DESC LIMIT ? OFFSET ?
        ''', (category_id, config.PER_PAGE, offset))
        articles = cursor.fetchall()
        return render_template('index.html', categories=categories, articles=articles, current_category=category_id,
                              page=page, total_pages=total_pages, total=total, per_page=config.PER_PAGE)


@front_bp.route('/article/<article_code>')
def article_detail(article_code):
    """文章详情页"""
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
            return '文章不存在', 404
        article = dict(row)
        cursor.execute('SELECT * FROM modules WHERE article_id = ? ORDER BY sort_order', (article['id'],))
        modules = [dict(r) for r in cursor.fetchall()]
        return render_template('article.html', article=article, modules=modules)
