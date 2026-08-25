# -*- coding: utf-8 -*-
"""为 #50「新大货快递报价表」建立独立的海外仓价格表数据副本。

从 data/warehouse_au/ 复制 allied/border/tfm/toll 四张表到 data/warehouse_au_dahuo/，
并生成只含这 4 项的 _index.json。#50 前台/后台通过 ?dir=warehouse_au_dahuo 读写此目录，
与海外仓文章 #47（data/warehouse_au/）数据隔离、互不影响。

幂等：直接覆盖复制。运行：./bjb_venv/bin/python scripts/init_dahuo_warehouse.py
"""
import json
import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, 'data', 'warehouse_au')
OUT_DIR = os.path.join(BASE_DIR, 'data', 'warehouse_au_dahuo')
KEYS = ['allied', 'border', 'tfm', 'toll']


def main():
    if not os.path.isdir(SRC_DIR):
        print(f'ERROR: source dir not found: {SRC_DIR}')
        sys.exit(1)
    os.makedirs(OUT_DIR, exist_ok=True)

    # 读源 _index.json，筛出 4 张表的 meta
    src_index_path = os.path.join(SRC_DIR, '_index.json')
    with open(src_index_path, 'r', encoding='utf-8') as f:
        src_index = json.load(f)
    meta_by_key = {e['key']: e for e in src_index}

    new_index = []
    for key in KEYS:
        src_sheet = os.path.join(SRC_DIR, f'{key}.json')
        if not os.path.isfile(src_sheet):
            print(f'ERROR: source sheet not found: {src_sheet}')
            sys.exit(1)
        shutil.copyfile(src_sheet, os.path.join(OUT_DIR, f'{key}.json'))
        meta = meta_by_key.get(key, {'key': key, 'name': key, 'row_count': 0, 'is_large': False})
        new_index.append({
            'key': key,
            'name': meta.get('name', key),
            'row_count': meta.get('row_count', 0),
            'is_large': meta.get('is_large', False),
        })
        print(f'  copied {key}.json ({meta.get("name")}, {meta.get("row_count")} rows)')

    with open(os.path.join(OUT_DIR, '_index.json'), 'w', encoding='utf-8') as f:
        json.dump(new_index, f, ensure_ascii=False, indent=2)
    print(f'  wrote _index.json ({len(new_index)} sheets) -> {OUT_DIR}')
    print('\nAll done!')


if __name__ == '__main__':
    main()
