# -*- coding: utf-8 -*-
"""新大货文章 border / toll 偏远附加费查询。

数据来源为 scripts/init_remote_surcharge.py 灌入的三张表：
  border_ras_fee / border_remote_postcode / toll_remote_postcode

对外只暴露 lookup(codes)：给定若干 4 位邮编，返回每个邮编在 border / toll 下的
偏远附加费明细（border 按 RAS 等级分组，toll 按费用分组）。查不到则 has=False。
"""
import logging

import db

_logger = logging.getLogger(__name__)

# suburb 列表最多回传条数，避免个别邮编（如 0872）返回上百个地区把响应撑大
_MAX_SUBURBS = 30


def _norm_codes(codes):
    out, seen = [], set()
    for c in (codes or []):
        digits = ''.join(ch for ch in str(c or '') if ch.isdigit())
        if len(digits) == 4 and digits not in seen:
            seen.add(digits)
            out.append(digits)
    return out


def _cap_suburbs(names):
    """去重保序 + 截断，返回 (list, total)。"""
    uniq, seen = [], set()
    for n in names:
        n = (n or '').strip()
        if n and n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq[:_MAX_SUBURBS], len(uniq)


def lookup(codes):
    """返回 { code: {"border": {...}, "toll": {...}} }。"""
    codes = _norm_codes(codes)
    result = {code: {'border': {'has': False, 'tiers': []},
                     'toll': {'has': False, 'groups': []}} for code in codes}
    if not codes:
        return result

    placeholders = ','.join('?' * len(codes))
    with db.get_db() as conn:
        cur = conn.cursor()

        # border 费率映射：ras_tier -> (parcel_fee, bulk_fee)
        cur.execute('SELECT ras_tier, parcel_fee, bulk_fee FROM border_ras_fee')
        fee_map = {row['ras_tier']: (row['parcel_fee'], row['bulk_fee']) for row in cur.fetchall()}

        # border：按 postcode + ras_tier 归组 suburb
        cur.execute(
            'SELECT postcode, ras_tier, suburb FROM border_remote_postcode '
            f'WHERE postcode IN ({placeholders})',
            codes,
        )
        border_group = {}  # code -> {tier -> [suburb,...]}
        for row in cur.fetchall():
            border_group.setdefault(row['postcode'], {}).setdefault(row['ras_tier'], []).append(row['suburb'])
        for code, tiers in border_group.items():
            items = []
            for tier in sorted(tiers):
                parcel_fee, bulk_fee = fee_map.get(tier, (None, None))
                suburbs, total = _cap_suburbs(tiers[tier])
                items.append({
                    'ras_tier': tier,
                    'parcel_fee': parcel_fee,
                    'bulk_fee': bulk_fee,
                    'suburbs': suburbs,
                    'count': total,
                })
            if items:
                result[code]['border'] = {'has': True, 'tiers': items}

        # toll：按 postcode + fee 归组 suburb
        cur.execute(
            'SELECT postcode, fee, suburb FROM toll_remote_postcode '
            f'WHERE postcode IN ({placeholders})',
            codes,
        )
        toll_group = {}  # code -> {fee -> [suburb,...]}
        for row in cur.fetchall():
            toll_group.setdefault(row['postcode'], {}).setdefault(row['fee'], []).append(row['suburb'])
        for code, fees in toll_group.items():
            groups = []
            for fee in sorted(fees):
                suburbs, total = _cap_suburbs(fees[fee])
                groups.append({'fee': fee, 'suburbs': suburbs, 'count': total})
            if groups:
                result[code]['toll'] = {'has': True, 'groups': groups}

    return result
