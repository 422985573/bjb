# -*- coding: utf-8 -*-
"""数据库连接与初始化"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

import config


def _connect():
    """内部：建立并返回一个连接"""
    path = config.DB_PATH if os.path.isabs(config.DB_PATH) else os.path.join(config._BASE_DIR, config.DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # 低风险增强：开启外键约束并设置忙等待，减少并发写入瞬时失败。
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA busy_timeout = 5000')
    return conn


@contextmanager
def get_db():
    """数据库连接上下文管理器：with get_db() as conn 确保异常时也关闭连接"""
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """初始化数据库"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_code TEXT UNIQUE,
            category_id INTEGER,
            title TEXT NOT NULL,
            is_published INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        )
    ''')
        cursor.execute("PRAGMA table_info(articles)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'article_code' not in columns:
            cursor.execute('ALTER TABLE articles ADD COLUMN article_code TEXT')
            cursor.execute('SELECT id, created_at FROM articles WHERE article_code IS NULL')
            articles = cursor.fetchall()
            for i, article in enumerate(articles):
                code = datetime.now().strftime('%Y%m%d') + str(i + 1).zfill(4)
                cursor.execute('UPDATE articles SET article_code = ? WHERE id = ?', (code, article['id']))
        if 'is_published' not in columns:
            cursor.execute('ALTER TABLE articles ADD COLUMN is_published INTEGER NOT NULL DEFAULT 1')
            cursor.execute('UPDATE articles SET is_published = 1 WHERE is_published IS NULL')
        if 'requires_phone_auth' not in columns:
            cursor.execute('ALTER TABLE articles ADD COLUMN requires_phone_auth INTEGER NOT NULL DEFAULT 0')
            cursor.execute('UPDATE articles SET requires_phone_auth = 0 WHERE requires_phone_auth IS NULL')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS phone_whitelist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS modules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER,
            type TEXT NOT NULL,
            content TEXT,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (article_id) REFERENCES articles(id)
        )
    ''')
        cursor.execute("DELETE FROM modules WHERE type = 'richtext'")
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS xiaobao_zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            postcode TEXT NOT NULL,
            zone TEXT NOT NULL,
            suburb TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_xiaobao_zones_postcode ON xiaobao_zones(postcode)')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS xiaobao_month_settings (
            month INTEGER PRIMARY KEY,
            unit_price REAL NOT NULL DEFAULT 0,
            exchange_rate REAL NOT NULL DEFAULT 0,
            fuel_rate REAL NOT NULL DEFAULT 0
        )
    ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS channel_reject_postcodes (
            channel TEXT PRIMARY KEY,
            postcodes TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
        cursor.execute('SELECT COUNT(*) FROM channel_reject_postcodes')
        if cursor.fetchone()[0] == 0:
            for channel_name, filename in config.CHANNEL_REJECT_POSTCODE_FILES.items():
                filepath = os.path.join(config._BASE_DIR, filename)
                postcodes_list = []
                if os.path.isfile(filepath):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        for line in f:
                            code = ''.join(c for c in line.strip() if c.isdigit())
                            if len(code) == 4:
                                postcodes_list.append(code)
                postcodes_str = ','.join(postcodes_list)
                cursor.execute(
                    'INSERT OR REPLACE INTO channel_reject_postcodes (channel, postcodes) VALUES (?, ?)',
                    (channel_name, postcodes_str)
                )
        for cat_name in ('大件拒收邮编', '纸箱拒收邮编'):
            cursor.execute('SELECT id FROM categories WHERE name = ?', (cat_name,))
            row = cursor.fetchone()
            if row:
                cid = row[0]
                cursor.execute('SELECT id FROM articles WHERE category_id = ?', (cid,))
                article_ids = [r[0] for r in cursor.fetchall()]
                for aid in article_ids:
                    cursor.execute('DELETE FROM modules WHERE article_id = ?', (aid,))
                cursor.execute('DELETE FROM articles WHERE category_id = ?', (cid,))
                cursor.execute('DELETE FROM categories WHERE id = ?', (cid,))
        conn.commit()
