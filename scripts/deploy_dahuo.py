# -*- coding: utf-8 -*-
"""上线部署脚本：在生产库上安全复现「新大货快递报价表」(#50) 的数据库内容。

幂等 + 非破坏：
  - 只新增/补齐本文章相关数据，不删除、不覆盖任何其它文章/分类/模块。
  - 重复运行不会重复创建（按分类名、article_code 判重）。
  - 生产库其它真实数据（客户报价、白名单等）完全不受影响。

它会：
  1. 确保分类「新大货快递报价文章」存在（不存在则创建，sort_order 取现有最大+1）。
  2. 确保文章 article_code=202608240001「新大货快递报价表」存在于该分类下。
  3. 若该文章还没有模块，则按导出的 dahuo_article.json 挂上 8 渠道 + 2 图片模块。
     （已有模块则跳过，避免重复；如需强制重建加 --force-modules）

运行（在服务器项目根目录）：
  ./bjb_venv/bin/python scripts/deploy_dahuo.py
  # 或宝塔里用项目的 python 解释器执行

配套：还需确保 data/warehouse_au_dahuo/ 目录已随代码上传（4 张表 json + _index.json + _settings.json）。
"""
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import db  # noqa: E402
import models  # noqa: E402

DATA_FILE = os.path.join(BASE_DIR, 'scripts', 'deploy_data', 'dahuo_article.json')
FORCE_MODULES = '--force-modules' in sys.argv


def main():
    if not os.path.isfile(DATA_FILE):
        print('ERROR: 缺少数据文件 %s（请随代码一起上传）' % DATA_FILE)
        sys.exit(1)
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        payload = json.load(f)

    cat_name = payload['category_name']
    art = payload['article']
    mods = payload['modules']

    db.init_db()
    with db.get_db() as conn:
        cur = conn.cursor()

        # 1. 分类
        cur.execute('SELECT id FROM categories WHERE name = ?', (cat_name,))
        row = cur.fetchone()
        if row:
            category_id = row[0]
            print('分类已存在：%s (id=%s)' % (cat_name, category_id))
        else:
            cur.execute('SELECT COALESCE(MAX(sort_order), 0) + 1 FROM categories')
            sort_order = cur.fetchone()[0]
            cur.execute('INSERT INTO categories (name, sort_order) VALUES (?, ?)', (cat_name, sort_order))
            category_id = cur.lastrowid
            print('已创建分类：%s (id=%s, sort_order=%s)' % (cat_name, category_id, sort_order))

        # 2. 文章（按 article_code 判重）
        cur.execute('SELECT id FROM articles WHERE article_code = ?', (art['article_code'],))
        row = cur.fetchone()
        if row:
            article_id = row[0]
            # 确保分类正确
            cur.execute('UPDATE articles SET category_id = ? WHERE id = ?', (category_id, article_id))
            print('文章已存在：%s (id=%s)，已确保归属分类' % (art['article_code'], article_id))
        else:
            cur.execute(
                'INSERT INTO articles (title, category_id, article_code, is_published) VALUES (?, ?, ?, ?)',
                (art['title'], category_id, art['article_code'], art.get('is_published', 1))
            )
            article_id = cur.lastrowid
            print('已创建文章：%s (id=%s)' % (art['article_code'], article_id))

        # 3. 模块（默认仅在为空时挂；--force-modules 则清空重建）
        cur.execute('SELECT COUNT(*) FROM modules WHERE article_id = ?', (article_id,))
        existing = cur.fetchone()[0]
        if existing and not FORCE_MODULES:
            print('文章已有 %s 个模块，跳过（如需重建加 --force-modules）' % existing)
        else:
            if existing:
                cur.execute('DELETE FROM modules WHERE article_id = ?', (article_id,))
                print('已清空原有 %s 个模块（--force-modules）' % existing)
            for m in mods:
                cur.execute(
                    'INSERT INTO modules (article_id, type, content, sort_order) VALUES (?, ?, ?, ?)',
                    (article_id, m['type'], m['content'], m['sort_order'])
                )
            print('已挂上 %s 个模块' % len(mods))

        conn.commit()

    # 4. 提示海外仓数据目录
    dahuo_dir = os.path.join(BASE_DIR, 'data', 'warehouse_au_dahuo')
    if os.path.isdir(dahuo_dir) and os.path.isfile(os.path.join(dahuo_dir, '_index.json')):
        print('海外仓数据目录 OK：%s' % dahuo_dir)
    else:
        print('警告：缺少 %s（4 张海外仓表 + _index.json + _settings.json），请随代码上传！' % dahuo_dir)

    print('\n完成。请重启服务后访问 /article/%s 验证。' % art['article_code'])


if __name__ == '__main__':
    main()
