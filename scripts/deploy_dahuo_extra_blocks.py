# -*- coding: utf-8 -*-
"""上线部署脚本：给「新大货快递报价表」(article_code=202608240001) 末尾补 6 块尾部模块。

补的 6 块（顺序，取自「2026年8月17日报价表」202608110001）：
  1. 产品品名详解      (surcharge)
  2. 国内附加明细      (surcharge_cn)
  3. 国外附加明细      (surcharge_intl)
  4. 常见海外仓地址    (overseas_warehouse)
  5. 锂电池出货要求    (image)
  6. 赔偿规则          (image)

幂等 + 非破坏：
  - 只在文章末尾「追加」缺失的块，绝不删除、不覆盖、不改动任何现有模块。
  - 判重：按「类型 + 标题」判重。已存在同类型同标题的块则跳过。
  - 重复运行安全：已存在的块自动跳过，只补缺的。
  - 不触碰其它文章。

运行（在服务器项目根目录）：
  ./bjb_venv/bin/python scripts/deploy_dahuo_extra_blocks.py

注意：图片模块的图片文件（/uploads/...）需已存在于生产环境（本脚本只写模块记录，不搬图片）。
"""
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import db  # noqa: E402

DATA_FILE = os.path.join(BASE_DIR, 'scripts', 'deploy_data', 'dahuo_extra_blocks.json')


def _title_of(content):
    try:
        return (json.loads(content) or {}).get('title', '') or ''
    except Exception:
        return ''


def main():
    if not os.path.isfile(DATA_FILE):
        print('ERROR: 缺少数据文件 %s（请随代码一起上传）' % DATA_FILE)
        sys.exit(1)
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        payload = json.load(f)

    article_code = payload['article_code']
    blocks = payload['blocks']

    db.init_db()
    with db.get_db() as conn:
        cur = conn.cursor()

        # 定位文章
        cur.execute('SELECT id FROM articles WHERE article_code = ?', (article_code,))
        row = cur.fetchone()
        if not row:
            print('ERROR: 未找到文章 article_code=%s，请先跑 deploy_dahuo.py' % article_code)
            sys.exit(1)
        article_id = row[0]
        print('目标文章 id=%s (article_code=%s)' % (article_id, article_code))

        # 现有模块快照（按 类型+标题 判重）
        cur.execute('SELECT type, content FROM modules WHERE article_id = ?', (article_id,))
        existing_keys = set()
        for t, ctt in cur.fetchall():
            existing_keys.add((t, _title_of(ctt)))

        cur.execute('SELECT COALESCE(MAX(sort_order), -1) FROM modules WHERE article_id = ?', (article_id,))
        next_sort = cur.fetchone()[0] + 1

        added = 0
        for b in blocks:
            btype = b['type']
            content = b['content']
            title = b.get('title_hint') or _title_of(content)
            key = (btype, title)

            if key in existing_keys:
                print('跳过（已存在同类型同标题）：%s | %s' % (btype, title))
                continue

            cur.execute(
                'INSERT INTO modules (article_id, type, content, sort_order) VALUES (?, ?, ?, ?)',
                (article_id, btype, content, next_sort)
            )
            print('已追加：%s | %s (sort=%s)' % (btype, title, next_sort))
            next_sort += 1
            added += 1
            existing_keys.add(key)

        conn.commit()

    print('\n完成，新增 %s 块。请重启服务后访问 /article/%s 验证。' % (added, article_code))


if __name__ == '__main__':
    main()
