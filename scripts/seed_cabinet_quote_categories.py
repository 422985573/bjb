#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从项目根目录的报价 xlsx 新增分类与模板文章。

模块内容为 DG **网格**（cells + merges），与 Excel 第一页表格布局、列字段、合并单元格严格一致
（含抬头区、「费用项目」表头行及列：价格固定项 / 费用项目 / 币别 / 20GP/金额 / 40HQ/金额 / 说明）。

用法（在项目根目录）：
  python3 scripts/seed_cabinet_quote_categories.py
  python3 scripts/seed_cabinet_quote_categories.py --force   # 分类已存在时仍按 xlsx 重写模块

分类名 = 文件名去掉 .xlsx；文章标题 = 「{分类名}报价表」。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import db  # noqa: E402
import models  # noqa: E402
from services.dg_quote_grid import cabinet_quote_xlsx_grid_content  # noqa: E402
from services.dg_excel_template_registry import DEFAULT_SEED_FILES  # noqa: E402

# 默认种子文件与 services/dg_excel_template_registry.py 中 EXCEL_QUOTE_TEMPLATE_ENTRIES 对齐
DEFAULT_FILES = list(DEFAULT_SEED_FILES)


def _next_category_sort_order(cursor) -> int:
    cursor.execute('SELECT COALESCE(MAX(sort_order), 0) FROM categories')
    return int(cursor.fetchone()[0]) + 1


def upsert_entry(cursor, rel_path: str, force: bool) -> None:
    abs_path = os.path.join(_ROOT, rel_path)
    stem = os.path.splitext(os.path.basename(rel_path))[0]
    article_title = f'{stem}报价表'

    cursor.execute('SELECT id FROM categories WHERE name = ?', (stem,))
    row = cursor.fetchone()
    if row:
        category_id = row[0]
        if not force:
            print(f'[跳过] 分类已存在：{stem}（使用 --force 可同步 xlsx 到 dg_grid）')
            return
        print(f'[更新] 分类已存在：{stem}，将按 xlsx 刷新 dg_grid…')
    else:
        sort_order = _next_category_sort_order(cursor)
        cursor.execute(
            'INSERT INTO categories (name, sort_order) VALUES (?, ?)',
            (stem, sort_order),
        )
        category_id = cursor.lastrowid
        print(f'[新建] 分类：{stem}（sort_order={sort_order}）')

    cursor.execute(
        'SELECT id FROM articles WHERE category_id = ? AND title = ?',
        (category_id, article_title),
    )
    ar = cursor.fetchone()
    if ar:
        article_id = ar[0]
    else:
        code = models.generate_article_code()
        cursor.execute(
            'INSERT INTO articles (title, category_id, article_code, is_published) VALUES (?, ?, ?, 1)',
            (article_title, category_id, code),
        )
        article_id = cursor.lastrowid
        print(f'  [新建] 文章：{article_title}（id={article_id}）')

    content = cabinet_quote_xlsx_grid_content(abs_path)
    cells = content.get('cells') or []
    hint = ''
    if cells and cells[0]:
        hint = str(cells[0][0] or '')
    if not cells or not isinstance(cells[0], list):
        print(f'  [错误] 未读到表格网格：{rel_path}')
        return
    if hint.startswith('未找到文件') or hint.startswith('未安装 openpyxl'):
        print(f'  [错误] {hint}')
        return

    payload = json.dumps(content, ensure_ascii=False)

    cursor.execute('SELECT id FROM modules WHERE article_id = ? ORDER BY sort_order, id', (article_id,))
    mods = [r[0] for r in cursor.fetchall()]
    if mods:
        cursor.execute(
            'UPDATE modules SET type = ?, content = ?, sort_order = 0 WHERE id = ?',
            ('dg_grid', payload, mods[0]),
        )
        for mid in mods[1:]:
            cursor.execute('DELETE FROM modules WHERE id = ?', (mid,))
        print(f'  [更新] dg_grid 模块（栅格，article_id={article_id}）')
    else:
        cursor.execute(
            'INSERT INTO modules (article_id, type, content, sort_order) VALUES (?, ?, ?, 0)',
            (article_id, 'dg_grid', payload),
        )
        print(f'  [新建] dg_grid 模块（栅格，article_id={article_id}）')


def main() -> None:
    parser = argparse.ArgumentParser(description='从 xlsx 新增柜类报价分类与 dg_grid（严格网格）文章')
    parser.add_argument(
        'files',
        nargs='*',
        default=list(DEFAULT_FILES),
        help='项目根目录下的 xlsx 文件名（默认：9类电池柜.xlsx 普柜.xlsx）',
    )
    parser.add_argument('--force', action='store_true', help='分类已存在时仍从 xlsx 刷新 dg_grid')
    args = parser.parse_args()

    with db.get_db() as conn:
        cursor = conn.cursor()
        for rel in args.files:
            name = os.path.basename(rel)
            if not os.path.isfile(os.path.join(_ROOT, name)):
                print(f'[跳过] 找不到文件：{name}')
                continue
            upsert_entry(cursor, name, args.force)
        conn.commit()
    print('完成。')


if __name__ == '__main__':
    main()
