# -*- coding: utf-8 -*-
"""初始化「虚拟小包报价表」文章数据。

流程：
  1. 解析 虚拟小包报价表.xlsx（复用 parse_warehouse_au.parse_aupost_dual）
  2. 写价格表 JSON 到 data/xiaobao/{eparcel.json,_index.json}
  3. 分区表（邮编→zone）写入数据库 xiaobao_zones（DELETE 后重灌，幂等）
  4. 幂等创建文章 + xiaobao_sheets 模块

运行方式: ./bjb_venv/bin/python scripts/init_xiaobao.py
"""
import json
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import openpyxl  # noqa: E402

import db  # noqa: E402
from scripts.parse_warehouse_au import parse_aupost_dual  # noqa: E402

EXCEL_PATH = os.path.join(BASE_DIR, '虚拟小包报价表.xlsx')
OUT_DIR = os.path.join(BASE_DIR, 'data', 'xiaobao')
KEY = 'eparcel'
SHEET_NAME = '虚拟小包报价表'
TITLE = '虚拟小包报价表'
CATEGORY_ID = 11

# 报价表上方的富文本说明（后台报价表编辑页可分块单独编辑）
INTRO_BLOCKS = [
    (
        '<p>报价说明：本公司专线空运小包报价采取：头程运输费用+尾程派送费用=最终全程运费'
        '（可批量粘贴邮编查询报价/目前只能落地至悉尼机场）</p>'
    ),
    (
        '<p><strong>头程运输费用详解：</strong></p>'
        '<p>1、费用包含国内运输费用+航空运费费用+航空燃油费用+目的港杂费，无隐藏收费；</p>'
        '<p>2、币种：人民币RMB；</p>'
        '<p>3、此渠道可运输货物为多品类，极为特殊品类需人工确认；</p>'
        '<p>4、此渠道可以满足24小时尾程快递第一条轨迹记录，第四天揽收轨迹；</p>'
        '<p>5、报价接受小数点后两位；</p>'
    ),
    (
        '<p><strong>尾程派送费用详解：</strong></p>'
        '<p>1、币别：澳元AUD；</p>'
        '<p>2、无法派送原件退回运费：$18/件；</p>'
        '<p>3、单件尺寸重量限制：最长边≤100cm，体积≤0.08m³（材积系数除4000或者体积*250）</p>'
        '<p>4、若实际发货最长边＞105cm，需额外加收：100 AUD/票（100澳币aupost可能收取 实报实销）；</p>'
        '<p>5、计费重：实重3KG及以下，按实重收费，3公斤以上按实重和材积取大收费实，材积系数4000；'
        '体积*250；实重3KG以上，但是申报3KG以下的，会产生100-300澳币的罚金（实报实销）</p>'
        '<p>6、报价已计算燃油，燃油费率：2018年9月1日起按月更新和收取燃油费用，点击查询官网燃油费率：'
        ' https://auspost.com.au/business/shipping/check-postage-costs/fuel-surcharge</p>'
        '<p>9、计费重量将按1kg进位，例：计费重为1.2kg,实际计费重为2kg；</p>'
    ),
]


def parse_excel():
    if not os.path.isfile(EXCEL_PATH):
        print(f'ERROR: Excel not found: {EXCEL_PATH}')
        sys.exit(1)
    wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
    ws = wb[wb.sheetnames[0]]
    price_sections, zone_section, _pzm = parse_aupost_dual(ws, SHEET_NAME)

    sections = []
    for ps in price_sections:
        if ps.get('type') == 'richtext':
            continue  # 丢弃 xlsx 自带的富文本（如底部 AUPOST 价格说明），只保留顶部自定义说明
        ps['type'] = 'price_table'
        sections.append(ps)
    # 在报价表上方插入可编辑的富文本说明（分块）
    intro = [{'title': '', 'type': 'richtext', 'html': h} for h in INTRO_BLOCKS]
    return intro + sections, zone_section


def write_json(sections):
    os.makedirs(OUT_DIR, exist_ok=True)
    total_rows = sum(len(s.get('rows', [])) for s in sections)
    data = {'name': SHEET_NAME, 'key': KEY, 'sections': sections, 'is_large': False}
    with open(os.path.join(OUT_DIR, f'{KEY}.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    index = [{'key': KEY, 'name': SHEET_NAME, 'row_count': total_rows, 'is_large': False}]
    with open(os.path.join(OUT_DIR, '_index.json'), 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f'  JSON: {total_rows} price rows -> {OUT_DIR}')


def seed_zones(zone_section):
    if not zone_section or not zone_section.get('rows'):
        print('  WARN: no zone rows to seed')
        return
    headers = [str(h).strip().lower() for h in zone_section.get('headers', [])]

    def _col(*names):
        for n in names:
            if n in headers:
                return headers.index(n)
        return None

    pc_idx = _col('postcodes', 'postcode', '邮编')
    zone_idx = _col('zone', '分区')
    suburb_idx = _col('suburb', 'name')
    state_idx = _col('state', '州')
    if pc_idx is None or zone_idx is None:
        print(f'  ERROR: cannot locate postcode/zone columns in {headers}')
        sys.exit(1)

    rows = []
    for r in zone_section['rows']:
        raw_pc = r[pc_idx] if pc_idx < len(r) else ''
        digits = ''.join(c for c in str(raw_pc) if c.isdigit())
        if not digits or len(digits) > 4:
            continue
        postcode = digits.zfill(4)
        zone = str(r[zone_idx]).strip() if zone_idx < len(r) else ''
        if not zone:
            continue
        suburb = str(r[suburb_idx]).strip() if suburb_idx is not None and suburb_idx < len(r) else ''
        state = str(r[state_idx]).strip() if state_idx is not None and state_idx < len(r) else ''
        rows.append((postcode, zone, suburb, state))

    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM xiaobao_zones')
        cur.executemany(
            'INSERT INTO xiaobao_zones (postcode, zone, suburb, state) VALUES (?, ?, ?, ?)',
            rows,
        )
        conn.commit()
    print(f'  Zones: seeded {len(rows)} rows into xiaobao_zones')


def seed_month_settings():
    """幂等：为 12 个月创建默认参数（已存在则保留）。默认 单价30 / 汇率4.6 / 燃油5.2%"""
    with db.get_db() as conn:
        cur = conn.cursor()
        for m in range(1, 13):
            cur.execute(
                'INSERT OR IGNORE INTO xiaobao_month_settings (month, unit_price, exchange_rate, fuel_rate) '
                'VALUES (?, ?, ?, ?)',
                (m, 30, 4.6, 5.2),
            )
        conn.commit()
    print('  Month settings: ensured 12 months exist')


def seed_article():
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id, article_code FROM articles WHERE title = ?', (TITLE,))
        existing = cur.fetchone()
        if existing:
            article_id = existing['id']
            print(f'  Article exists: id={article_id}, code={existing["article_code"]}')
        else:
            today = datetime.now().strftime('%Y%m%d')
            cur.execute("SELECT COUNT(*) FROM articles WHERE article_code LIKE ?", (today + '%',))
            count = cur.fetchone()[0]
            article_code = today + str(count + 1).zfill(4)
            cur.execute(
                'INSERT INTO articles (title, category_id, article_code, is_published) VALUES (?, ?, ?, ?)',
                (TITLE, CATEGORY_ID, article_code, 1)
            )
            article_id = cur.lastrowid
            print(f'  Created article: id={article_id}, code={article_code}')

        cur.execute("SELECT id FROM modules WHERE article_id = ? AND type = 'xiaobao_sheets'", (article_id,))
        if cur.fetchone():
            print('  Module exists, skipping')
        else:
            content = json.dumps({'data_dir': 'xiaobao'}, ensure_ascii=False)
            cur.execute(
                'INSERT INTO modules (article_id, type, content, sort_order) VALUES (?, ?, ?, ?)',
                (article_id, 'xiaobao_sheets', content, 0)
            )
            print(f'  Created xiaobao_sheets module: id={cur.lastrowid}')
        conn.commit()


def main():
    print('Step 0: ensure DB schema...')
    db.init_db()
    print('Step 1: parse Excel...')
    sections, zone_section = parse_excel()
    print('Step 2: write price JSON...')
    write_json(sections)
    print('Step 3: seed zone table (DB)...')
    seed_zones(zone_section)
    print('Step 3b: seed month settings...')
    seed_month_settings()
    print('Step 4: seed article + module...')
    seed_article()
    print('\nAll done!')


if __name__ == '__main__':
    main()
