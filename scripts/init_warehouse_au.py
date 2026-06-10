# -*- coding: utf-8 -*-
"""初始化澳洲海外仓文章数据：解析 Excel + 插入数据库记录。
运行方式: python3 scripts/init_warehouse_au.py
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

DB_PATH = os.path.join(BASE_DIR, 'data', 'database.db')
TITLE = '璟川森特（澳洲海外仓）报价-2026.5. 10日（Aupost : iMile 3kg以内实重计费）'
CATEGORY_ID = 11


def seed_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id FROM articles WHERE title = ?", (TITLE,))
    existing = cur.fetchone()
    if existing:
        article_id = existing[0]
        print(f"Article already exists: id={article_id}")
    else:
        today = datetime.now().strftime('%Y%m%d')
        cur.execute("SELECT COUNT(*) FROM articles WHERE article_code LIKE ?", (today + '%',))
        count = cur.fetchone()[0]
        article_code = today + str(count + 1).zfill(4)
        cur.execute(
            "INSERT INTO articles (title, category_id, article_code, is_published) VALUES (?, ?, ?, ?)",
            (TITLE, CATEGORY_ID, article_code, 1)
        )
        article_id = cur.lastrowid
        print(f"Created article: id={article_id}, code={article_code}")

    cur.execute("SELECT id FROM modules WHERE article_id = ? AND type = 'warehouse_sheets'", (article_id,))
    if cur.fetchone():
        print("Module already exists, skipping")
    else:
        content = json.dumps({"data_dir": "warehouse_au"}, ensure_ascii=False)
        cur.execute(
            "INSERT INTO modules (article_id, type, content, sort_order) VALUES (?, ?, ?, ?)",
            (article_id, "warehouse_sheets", content, 0)
        )
        print(f"Created warehouse_sheets module: id={cur.lastrowid}")

    conn.commit()
    conn.close()


def main():
    print("Step 1: Parsing Excel...")
    from scripts.parse_warehouse_au import main as parse_main
    parse_main()

    print("\nStep 2: Seeding database...")
    seed_db()

    print("\nAll done!")


if __name__ == '__main__':
    main()
