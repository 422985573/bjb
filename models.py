# -*- coding: utf-8 -*-
"""数据访问：分类、文章、模块等"""
from datetime import datetime

import config
import db


def get_category_id_by_name(name):
    """按分类名称获取 id，不存在则返回 None"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM categories WHERE name = ?', (name,))
        row = cursor.fetchone()
        return row[0] if row else None


def get_all_categories():
    """获取所有分类（按 sort_order），返回 list[dict]"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM categories ORDER BY sort_order, id')
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def allowed_file(filename):
    """检查文件扩展名是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in config.ALLOWED_EXTENSIONS


def generate_article_code():
    """生成文章编号：年月日+4位序号"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        today = datetime.now().strftime('%Y%m%d')
        cursor.execute("SELECT COUNT(*) FROM articles WHERE article_code LIKE ?", (today + '%',))
        count = cursor.fetchone()[0]
        seq = str(count + 1).zfill(4)
        return today + seq
