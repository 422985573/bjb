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


def _split_shared_xiaobao_sheets(cursor):
    """一次性迁移：把历史上「共用同一价格表」的多篇小包文章各拆成独立 sheet 文件。

    幂等且并发安全：新 key 取 `{原key}_{article_id}`（每篇文章唯一、确定），
    多 worker 并发运行会收敛到相同结果；文件写入用 atomic_write_json，
    整个文件区段用 data_dir_lock 串行化。只有 >=2 篇小包文章时才可能有共用。
    """
    import json as _json
    import re as _re
    from util import atomic_write_json, data_dir_lock

    base = os.path.join(config._BASE_DIR, 'data', 'xiaobao')
    index_path = os.path.join(base, '_index.json')
    if not os.path.isfile(index_path):
        return
    cursor.execute(
        "SELECT m.id AS mid, m.article_id AS aid, m.content AS content, a.title AS title "
        "FROM modules m LEFT JOIN articles a ON a.id = m.article_id "
        "WHERE m.type = 'xiaobao_sheets' ORDER BY m.article_id"
    )
    mods = cursor.fetchall()
    if len(mods) <= 1:
        return

    def _parse(content):
        try:
            c = _json.loads(content) if content else {}
        except (TypeError, ValueError, _json.JSONDecodeError):
            c = {}
        return c if isinstance(c, dict) else {}

    with data_dir_lock(base):
        with open(index_path, 'r', encoding='utf-8') as f:
            index = _json.load(f)
        if not index:
            return
        default_key = index[0].get('key')          # 老库单例回退键
        index_keys = {e.get('key') for e in index if e.get('key')}
        claimed = set()                             # 已被某篇文章独占的 key
        index_changed = False

        for m in mods:
            c = _parse(m['content'])
            key = c.get('sheet_key') or default_key
            if not key:
                continue
            if key not in claimed:
                # 首个占用该 key 的文章保留它；若模块缺 sheet_key 则回填
                claimed.add(key)
                if not c.get('sheet_key'):
                    c['sheet_key'] = key
                    cursor.execute('UPDATE modules SET content = ? WHERE id = ?',
                                   (_json.dumps(c, ensure_ascii=False), m['mid']))
                continue
            # 该 key 已被更早的文章占用 → 为本篇拆一份独立副本（key 确定，便于收敛）
            new_key = f'{key}_{m["aid"]}'
            src_safe = _re.sub(r'[^a-zA-Z0-9_]', '', key)
            new_safe = _re.sub(r'[^a-zA-Z0-9_]', '', new_key)
            src_path = os.path.join(base, f'{src_safe}.json')
            new_path = os.path.join(base, f'{new_safe}.json')
            if not os.path.isfile(src_path):
                continue
            if not os.path.isfile(new_path):
                with open(src_path, 'r', encoding='utf-8') as f:
                    sheet_data = _json.load(f)
                if m['title']:
                    sheet_data['name'] = m['title']
                atomic_write_json(new_path, sheet_data, indent=2)
            if new_key not in index_keys:
                src_entry = next((dict(e) for e in index if e.get('key') == key), {})
                src_entry['key'] = new_key
                if m['title']:
                    src_entry['name'] = m['title']
                index.append(src_entry)
                index_keys.add(new_key)
                index_changed = True
            claimed.add(new_key)
            c['sheet_key'] = new_key
            cursor.execute('UPDATE modules SET content = ? WHERE id = ?',
                           (_json.dumps(c, ensure_ascii=False), m['mid']))
        if index_changed:
            atomic_write_json(index_path, index, indent=2)


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
        # 新大货文章 border/toll 偏远附加费（由 scripts/init_remote_surcharge.py 从两份 xlsx 灌库）
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS border_ras_fee (
            ras_tier TEXT PRIMARY KEY,
            parcel_fee REAL NOT NULL DEFAULT 0,
            bulk_fee REAL NOT NULL DEFAULT 0
        )
    ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS border_remote_postcode (
            postcode TEXT NOT NULL,
            suburb TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT '',
            zone TEXT NOT NULL DEFAULT '',
            ras_tier TEXT NOT NULL DEFAULT ''
        )
    ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_border_remote_postcode ON border_remote_postcode(postcode)')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS toll_remote_postcode (
            postcode TEXT NOT NULL,
            suburb TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT '',
            fee REAL NOT NULL DEFAULT 0
        )
    ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_toll_remote_postcode ON toll_remote_postcode(postcode)')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS xiaobao_month_settings (
            month INTEGER PRIMARY KEY,
            unit_price REAL NOT NULL DEFAULT 0,
            exchange_rate REAL NOT NULL DEFAULT 0,
            fuel_rate REAL NOT NULL DEFAULT 0,
            sea_unit_price REAL NOT NULL DEFAULT 0
        )
    ''')
        cursor.execute("PRAGMA table_info(xiaobao_month_settings)")
        xb_cols = [col[1] for col in cursor.fetchall()]
        if 'sea_unit_price' not in xb_cols:
            cursor.execute('ALTER TABLE xiaobao_month_settings ADD COLUMN sea_unit_price REAL NOT NULL DEFAULT 0')
        # 按文章隔离的小包参数（每篇文章各一行；单价/汇率/燃油费率/海运单价）
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS xiaobao_article_settings (
            article_id INTEGER PRIMARY KEY,
            unit_price REAL NOT NULL DEFAULT 0,
            exchange_rate REAL NOT NULL DEFAULT 0,
            fuel_rate REAL NOT NULL DEFAULT 0,
            sea_unit_price REAL NOT NULL DEFAULT 0
        )
    ''')
        # 一次性迁移：把旧的全局参数行（month=0）种入现有小包文章，保证显示不变。
        # 仅在 xiaobao_article_settings 尚无对应行时补种（幂等）。
        cursor.execute(
            'SELECT unit_price, exchange_rate, fuel_rate, sea_unit_price '
            'FROM xiaobao_month_settings WHERE month = 0'
        )
        _xb_global = cursor.fetchone()
        if _xb_global is not None:
            cursor.execute("SELECT article_id FROM modules WHERE type = 'xiaobao_sheets'")
            for _row in cursor.fetchall():
                cursor.execute(
                    'INSERT OR IGNORE INTO xiaobao_article_settings '
                    '(article_id, unit_price, exchange_rate, fuel_rate, sea_unit_price) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (_row[0], _xb_global[0], _xb_global[1], _xb_global[2], _xb_global[3]),
                )
        # 一次性迁移：把「共用同一价格表」的历史小包文章各拆成独立 sheet 文件。
        # 幂等：每篇文章模块 pin 独立 sheet_key 后重跑即跳过。仅在存在共用时才拆。
        _split_shared_xiaobao_sheets(cursor)
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
