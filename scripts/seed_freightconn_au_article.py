#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 Freightconn 澳洲专线清拆派 PDF 内容新增一篇文章。

后台希望以「单元格逐格编辑」，因此模块类型统一使用 `dg_grid` + `variant=excel_grid`
（与 9类电池柜 / 普柜报价表同套编辑器 UI）：cells 是二维数组，merges 描述合并区。
每个 PDF 分区拆成独立模块，方便后台单独增删改。

用法（项目根目录）：
  python3 scripts/seed_freightconn_au_article.py            # 已存在则跳过
  python3 scripts/seed_freightconn_au_article.py --force    # 已存在时按当前脚本重写
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
from services.dg_quote_grid import normalize_excel_grid_content_storage  # noqa: E402


ARTICLE_TITLE = 'Freightconn澳洲专线清拆派'
CATEGORY_NAME = '专线报价文章'
# 数据库里已把该文章挂到 id=16 分类（外部约定）。脚本 --force 时也保持这个映射。
CATEGORY_ID_OVERRIDE = 16


def _grid(title, cells, merges=None, remark=''):
    """构造 excel_grid dg_grid content 字典。"""
    n = len(cells)
    m = max((len(r) for r in cells if r), default=1) if n else 1
    padded = [list(r) + [''] * (m - len(r)) for r in cells]
    return {
        'variant': 'excel_grid',
        'template_id': '',
        'title': title,
        'template_file': '',
        'sheet_name': '',
        'rows': n,
        'cols': m,
        'cells': padded,
        'merges': list(merges or []),
        'header_orange_row': None,
        'remark': remark,
    }


def build_air_grid():
    cells = [
        ['CODE', '每个空运主单', '', 'SYD', 'UOM'],
        ['1、', 'Arrival - Air', '文件费', '$200.00', 'PER AWB'],
        ['', '', '机场费 散货(最低消费 200kg)', '$0.8+$67', 'PER CHARGEABLE KG'],
        ['', '', '机场费 整板或空运集装箱', '$0.30', 'PER CHARGEABLE KG'],
        ['', '', '提货费', '$0.20', 'PER CHARGEABLE KG'],
        ['2、', 'Breakdown - Air', '仓库扫描处理发货费', '$0.35', 'PER CHARGEABLE KG'],
        ['3、', 'Surcharge - Air', '周末提货附加费', '$0.25', 'PER CHARGEABLE KG'],
        ['', '', '机场仓储费', '$0.40', 'PER CHAGEABLE KG / DAY'],
    ]
    merges = [
        {'r': 0, 'c': 1, 'rs': 1, 'cs': 2},   # 表头「每个空运主单」跨 category+item
        {'r': 1, 'c': 0, 'rs': 4, 'cs': 1},   # 1、
        {'r': 1, 'c': 1, 'rs': 4, 'cs': 1},   # Arrival - Air
        {'r': 6, 'c': 0, 'rs': 2, 'cs': 1},   # 3、
        {'r': 6, 'c': 1, 'rs': 2, 'cs': 1},   # Surcharge - Air
    ]
    return _grid('一、Arrival by Air EX SYD 空运操作费用', cells, merges)


def build_sea_grid():
    cells = [
        ['CODE', '40尺海运柜', '', 'SYD', 'MEL', 'UOM'],
        ['1、', 'Arrival - Sea', '海运文件费', '$300.00', '', 'PER BL'],
        ['', '', '船公司码头费', 'At cost (约$980)', 'At cost (约$980)', 'PER BL'],
        ['', '', '码头 Time Slot', '$70.00', '', 'PER CONTAINER'],
        ['', '', '提柜拖柜费到 Freightconn 仓库', '$1300.00', '$1300.00', 'PER CONTAINER'],
        ['', '', '燃油附加费（仅限拖柜送货费）', '16%', '', '/'],
        ['', '', '还柜', '$50.00', '', 'PER CONTAINER'],
        ['2、', 'Breakdown - Sea LCL', '拆柜 按件数 <800pcs', '$900.00', '$900.00', 'PER CONTAINER'],
        ['', '', '拆柜 按件数 <1000pcs', '$1100.00', '$1100.00', 'PER CONTAINER'],
        ['', '', '拆柜 按件数 <1200pcs', '$1300.00', '$1300.00', 'PER CONTAINER'],
        ['', '', '打板+缠膜（含板）', '$25.00/个', '$25.00/个', 'PER PIECE'],
        ['3、', 'Breakdown - Sea FCL', '拆柜 按件数 <800pcs', '$700.00', '$700.00', 'PER CONTAINER'],
        ['', '', '拆柜 按件数 <1000pcs', '$900.00', '$900.00', 'PER CONTAINER'],
        ['', '', '拆柜 按件数 <1200pcs', '$1100.00', '$1200.00', 'PER CONTAINER'],
        ['', '', '打板+缠膜（含板）', '$25.00/个', '$25.00/个', 'PER PIECE'],
        ['4、', 'Surcharge - Sea', '超尺寸卸柜费- 单件单边超过2m', '$0.00', '$0.00', 'PER CBM'],
        ['', '', '超尺寸卸柜费- 单件单边超过3m', '$0.00', '$0.00', 'PER CBM'],
        ['', '', '超尺寸卸柜费- 单件单边超过4m', '$0.00', '', 'PER CBM'],
        ['', '', '自提出库操作费（非Freightconn派送自提件）', '$25/托', '$25/托', 'PER PIECE'],
    ]
    merges = [
        {'r': 0, 'c': 1, 'rs': 1, 'cs': 2},   # 表头「40尺海运柜」跨 category+item
        # Arrival - Sea 分组
        {'r': 1, 'c': 0, 'rs': 6, 'cs': 1},
        {'r': 1, 'c': 1, 'rs': 6, 'cs': 1},
        {'r': 1, 'c': 3, 'rs': 1, 'cs': 2},   # 海运文件费 $300 跨 SYD/MEL
        {'r': 3, 'c': 3, 'rs': 1, 'cs': 2},   # Time Slot $70 跨 SYD/MEL
        {'r': 5, 'c': 3, 'rs': 1, 'cs': 2},   # 燃油附加费 16% 跨 SYD/MEL
        {'r': 6, 'c': 3, 'rs': 1, 'cs': 2},   # 还柜 $50 跨 SYD/MEL
        # Breakdown - Sea LCL
        {'r': 7, 'c': 0, 'rs': 4, 'cs': 1},
        {'r': 7, 'c': 1, 'rs': 4, 'cs': 1},
        # Breakdown - Sea FCL
        {'r': 11, 'c': 0, 'rs': 4, 'cs': 1},
        {'r': 11, 'c': 1, 'rs': 4, 'cs': 1},
        # Surcharge - Sea
        {'r': 15, 'c': 0, 'rs': 4, 'cs': 1},
        {'r': 15, 'c': 1, 'rs': 4, 'cs': 1},
        {'r': 17, 'c': 3, 'rs': 1, 'cs': 2},  # 超过4m $0 跨 SYD/MEL
    ]
    return _grid('二、Arrival by Sea 海运集装箱操作费用（40 尺海运柜）', cells, merges)


def build_storage_grid():
    cells = [
        ['项目', '说明', '费率'],
        [
            'Airfreight Storage after check-in\n空运滞仓费',
            'Per HAWB not despatched within 3 business days after check-in into '
            'Bonded Warehouse\n每票自空运抵达我司监管仓后 3 天（工作日）起计滞仓费',
            'AUD 0.20/kg/day\nfrom the 3rd day after arrival\n'
            '-DDU 收收件人，DDP 收发件人',
        ],
        [
            'Seafreight Storage after Check-in\n(for 3rd party Customs '
            'Clearance & Pick up Only)\n海运滞仓费（仅限自清自提货物）',
            'Per HBL not despatched within 3 business days after available into '
            'Bonded Warehouse\n每票自拆出后 3 天（工作日）起计滞仓费',
            'AUD 80/Shipment + AUD 30/cbm/day\nfrom the 3rd day after available\n'
            '-DDU 收收件人，DDP 收发件人',
        ],
    ]
    return _grid('Storage 滞仓费', cells, merges=[])


def build_customs_grid():
    cells = [
        ['项目', '价格', '计费单位'],
        ['SAC 每个 house 低于 AUD 1,000', '$1.00', 'per house'],
        ['SAC AQIS', '$50.00', 'per house'],
        ['高价值每个 house 高于 AUD 1,000', '$100.00', 'per house（含 10 行 HSCODE）'],
        ['', '$2 / Hscode & $275 Cap.', '超过 10 行 HS CODE 之后'],
        ['Duty / GST', 'At Cost', '/'],
        [
            'AQIS Formal Inspection Fee 商检检查费用',
            'Per HAWB Inspection Requested by DAFF to be carried out at licenced '
            'depot\n商检局指定要求检查件，在指定场所进行\n*不含送检提回运费约 AUD 100',
            'AUD 130 + AUD 30\nwithin half an hour, otherwise',
        ],
        [
            'Held Shipment Abandonment Fee 扣件弃件费',
            "Held shipment can't be cleared or cleared within 30 days after arrival"
            '\n海关扣件货物无法清关或不能在入仓后 30（自然）天内清关而需按照海关'
            '指定要求销毁弃件',
            'AUD 200/Shipment + AUD 20/kg',
        ],
        [
            'Cleared Shipment Abandonment Fee 清关件弃件费',
            'Cleared shipment to be abandoned\n货物清关后需销毁弃件的',
            'AUD 20/Shipment + AUD 0.35/kg',
        ],
    ]
    merges = [
        {'r': 3, 'c': 0, 'rs': 2, 'cs': 1},   # 高价值 house 项目跨两行
    ]
    remark = (
        '<p>1. 以上报价均为澳币</p>'
        '<p>2. 以上报价不含运输保险，丢失件不理赔</p>'
        '<p>3. 价格有效从报价日起至下次更新</p>'
        '<p>4. 所有报价不含 GST 10%</p>'
    )
    return _grid('Custom Clearance / 清关及海关费用', cells, merges, remark=remark)


def build_delivery_grid():
    cells = [
        ['Item', 'Rate', 'Unit'],
        ['Labelling', '0.5', 'Per Label'],
        ['Pallet', '25', 'Per Pallet'],
        ['SYD / MEL 亚马逊送仓费用', '', ''],
        ['BWU1、BWU2、XBW3、XAU1、BWU6 Delivery', '100', '1 Pallet'],
        ['', '80', '2 Pallets'],
        ['', '60', '3 - 6 Pallets'],
        ['', '50', '7 Pallets +'],
        ['SYD - MEL 洲际卡车 亚马逊送仓费用', '', ''],
        ['MEL1、MEL5、XAU1、AVV2、XAU2、XME6 Delivery', '180', '10 Pallets +'],
        ['SYD - BNE 洲际卡车 亚马逊送仓费用', '', ''],
        ['BNE1、XAU6 Delivery', '240', '10 Pallets +'],
        ['SYD / MEL 卡车 派送费用', '', ''],
        [
            '市区 30KM 内 Delivery',
            '30（不含燃油，燃油附加费 23.5%）min: AUD 135',
            '1 Pallets +',
        ],
        [
            '市区 60KM 内 Delivery',
            '60（不含燃油，燃油附加费 23.5%）min: AUD 135',
            '1 Pallets +',
        ],
    ]
    merges = [
        {'r': 4, 'c': 0, 'rs': 4, 'cs': 1},   # BWU Delivery 跨 4 行
    ]
    remark = (
        '<p>注意：</p>'
        '<p>1、每次洲际配送至少需要 10 个托盘</p>'
        '<p>2、前往墨尔本和布里斯班的州际运输需缴纳 23.5% 的燃油税</p>'
        '<p>3、7 天自然日免费存储</p>'
        '<p>4、卡车送货价格不包括燃油附加费</p>'
    )
    return _grid('车队派送（亚马逊送仓 / 60KM 内卡车派送）', cells, merges, remark=remark)


MODULE_BUILDERS = [
    build_air_grid,
    build_sea_grid,
    build_storage_grid,
    build_customs_grid,
    build_delivery_grid,
]


def _get_or_create_category(cursor):
    # 优先使用外部指定的 category_id，允许缺失（走「强制更新」路径）。
    if CATEGORY_ID_OVERRIDE is not None:
        cursor.execute('SELECT id FROM categories WHERE id = ?', (CATEGORY_ID_OVERRIDE,))
        row = cursor.fetchone()
        if row:
            return row[0]
        print(f'[提示] 分类 id={CATEGORY_ID_OVERRIDE} 不存在，将强制写入 article.category_id={CATEGORY_ID_OVERRIDE}（外键校验已关闭）。')
        return CATEGORY_ID_OVERRIDE
    cursor.execute('SELECT id FROM categories WHERE name = ?', (CATEGORY_NAME,))
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute('SELECT COALESCE(MAX(sort_order), 0) FROM categories')
    sort_order = int(cursor.fetchone()[0]) + 1
    cursor.execute(
        'INSERT INTO categories (name, sort_order) VALUES (?, ?)',
        (CATEGORY_NAME, sort_order),
    )
    print(f'[新建] 分类：{CATEGORY_NAME}（sort_order={sort_order}）')
    return cursor.lastrowid


def _upsert_article(cursor, category_id, force):
    cursor.execute(
        'SELECT id, article_code FROM articles WHERE title = ? AND category_id = ?',
        (ARTICLE_TITLE, category_id),
    )
    row = cursor.fetchone()
    if row:
        if not force:
            print(f'[跳过] 文章已存在：{ARTICLE_TITLE}（使用 --force 可重写模块）')
            return row[0], False
        print(f'[更新] 文章已存在，将重写模块：{ARTICLE_TITLE}')
        return row[0], True
    code = models.generate_article_code()
    cursor.execute(
        'INSERT INTO articles (title, category_id, article_code, is_published) '
        'VALUES (?, ?, ?, 1)',
        (ARTICLE_TITLE, category_id, code),
    )
    article_id = cursor.lastrowid
    print(f'[新建] 文章：{ARTICLE_TITLE}（id={article_id}, code={code}）')
    return article_id, True


def _reset_modules(cursor, article_id, builders):
    cursor.execute('DELETE FROM modules WHERE article_id = ?', (article_id,))
    for idx, builder in enumerate(builders):
        content = builder()
        normalize_excel_grid_content_storage(content)
        payload = json.dumps(content, ensure_ascii=False)
        cursor.execute(
            'INSERT INTO modules (article_id, type, content, sort_order) '
            'VALUES (?, ?, ?, ?)',
            (article_id, 'dg_grid', payload, idx),
        )
    print(f'  [写入] 模块 {len(builders)} 个（dg_grid / excel_grid）')


def main():
    parser = argparse.ArgumentParser(description='新增 Freightconn 澳洲专线文章')
    parser.add_argument('--force', action='store_true', help='文章已存在时重写模块')
    args = parser.parse_args()

    with db.get_db() as conn:
        cursor = conn.cursor()
        # 允许把文章挂到暂时不存在的 category_id（例如外部约定 id=16）。
        if CATEGORY_ID_OVERRIDE is not None:
            cursor.execute('PRAGMA foreign_keys = OFF')
        category_id = _get_or_create_category(cursor)
        article_id, do_reset = _upsert_article(cursor, category_id, args.force)
        if do_reset:
            _reset_modules(cursor, article_id, MODULE_BUILDERS)
        conn.commit()
    print('完成。')


if __name__ == '__main__':
    main()
