# -*- coding: utf-8 -*-
"""邮编服务：规范化、拒收集合、本地 CSV 距离查询。"""
import csv
import logging
import os

import config
import db

_logger = logging.getLogger(__name__)
_POSTCODE_DISTANCE_INDEX = None
_POSTCODE_DISTANCE_INDEX_MTIME = None


def normalize_postcode(raw):
    """仅接受严格 4 位数字邮编，非法则返回 None。"""
    d = ''.join(c for c in str(raw or '').strip() if c.isdigit())
    return d if len(d) == 4 and d == str(raw or '').strip() else None


def get_reject_postcode_set():
    """获取所有渠道拒收邮编的并集（4位数字字符串集合）"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT postcodes FROM channel_reject_postcodes')
        rows = cursor.fetchall()
        result = set()
        for row in rows:
            for part in (row[0] or '').replace('\n', ',').split(','):
                code = ''.join(c for c in part.strip() if c.isdigit())
                if len(code) == 4:
                    result.add(code)
        return result


def get_reject_postcodes_by_channel():
    """按渠道返回拒收邮编集合：{'大件': set(), '纸箱': set()}"""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT channel, postcodes FROM channel_reject_postcodes')
        rows = cursor.fetchall()
        result = {}
        for row in rows:
            ch, postcodes = row[0], (row[1] or '')
            s = set()
            for part in postcodes.replace('\n', ',').split(','):
                code = ''.join(c for c in part.strip() if c.isdigit())
                if len(code) == 4:
                    s.add(code)
            result[ch] = s
        for ch in ('大件', '纸箱'):
            result.setdefault(ch, set())
        return result


def _normalize_csv_postcode(value):
    code = ''.join(c for c in str(value or '').strip() if c.isdigit())
    return code if len(code) == 4 else None


def _parse_distance(value):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _load_postcode_distance_index():
    global _POSTCODE_DISTANCE_INDEX, _POSTCODE_DISTANCE_INDEX_MTIME

    path = config.POSTCODE_CSV_PATH
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f'postcode csv not found: {path}')

    mtime = os.path.getmtime(path)
    if _POSTCODE_DISTANCE_INDEX is not None and _POSTCODE_DISTANCE_INDEX_MTIME == mtime:
        return _POSTCODE_DISTANCE_INDEX

    index = {}
    with open(path, 'r', encoding='utf-8-sig', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            postcode = _normalize_csv_postcode(row.get('Postcode'))
            if not postcode:
                continue
            distance = _parse_distance(row.get('Distance_km'))
            index[postcode] = {
                'postcode': postcode,
                'warehouse_region': (row.get('Warehouse_Region') or '').strip(),
                'distance': distance,
                'distance_category': (row.get('Distance_Category') or '').strip(),
                'distance_unit': 'km',
                'is_serviceable': distance is not None,
            }

    _POSTCODE_DISTANCE_INDEX = index
    _POSTCODE_DISTANCE_INDEX_MTIME = mtime
    _logger.info('postcode csv loaded path=%s rows=%s', path, len(index))
    return index


def fetch_postcode_distance(key):
    """从本地 CSV 查询邮编距离。"""
    try:
        index = _load_postcode_distance_index()
        row = index.get(str(key).strip())
        if not row:
            return {
                'status': 'no_service',
                'code': 200,
                'message': '未找到该邮编的距离信息',
                'data': None,
                'postcode': str(key).strip(),
            }, None

        return {
            'status': 'success',
            'code': 200,
            'data': row,
        }, None
    except Exception as e:
        _logger.warning('postcode csv lookup failed: %s', e)
        return None, 'POSTCODE_DATA_UNAVAILABLE'
