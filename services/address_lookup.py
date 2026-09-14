# -*- coding: utf-8 -*-
"""第三方地址接口代理服务（供顶部搜索框输入地址时使用）。

两步计费一次会话：
  1) autocomplete(q)            把模糊地址变候选列表，返回里带 session
  2) detail(place_id, session)  用选中的 placeId + 同一 session 拿规范地址/住宅商业判定/图片

API Key 只在后端持有（config.ADDRESS_API_KEY），绝不下发前端。所有函数统一返回
(data, error)：error 为 None 表示成功；否则为简短错误码字符串，路由据此回错。
"""
import logging

import requests

import config

_logger = logging.getLogger(__name__)


def _headers():
    return {'X-Api-Key': config.ADDRESS_API_KEY, 'Accept': 'application/json'}


def _get(path, params=None, stream=False):
    url = config.ADDRESS_API_BASE + path
    return requests.get(url, params=params, headers=_headers(),
                        timeout=config.ADDRESS_API_TIMEOUT, stream=stream)


def autocomplete(q):
    """模糊地址 → 候选列表（含 session）。返回 (json, error)。"""
    try:
        resp = _get('/v1/addresses/autocomplete', params={'q': q, 'limit': 8})
        if resp.status_code != 200:
            _logger.warning('address autocomplete upstream %s: %s', resp.status_code, resp.text[:200])
            return None, 'UPSTREAM_%s' % resp.status_code
        return resp.json(), None
    except requests.Timeout:
        return None, 'UPSTREAM_TIMEOUT'
    except Exception as e:
        _logger.warning('address autocomplete failed: %s', e)
        return None, 'UPSTREAM_ERROR'


def detail(place_id, session=None):
    """placeId + session → 规范地址/住宅商业判定/图片信息。返回 (json, error)。"""
    params = {'image': 1}
    if session:
        params['session'] = session
    try:
        resp = _get('/v1/addresses/%s/detail' % place_id, params=params)
        if resp.status_code != 200:
            _logger.warning('address detail upstream %s: %s', resp.status_code, resp.text[:200])
            return None, 'UPSTREAM_%s' % resp.status_code
        return resp.json(), None
    except requests.Timeout:
        return None, 'UPSTREAM_TIMEOUT'
    except Exception as e:
        _logger.warning('address detail failed: %s', e)
        return None, 'UPSTREAM_ERROR'


def image(place_id, width=800):
    """建筑图片二进制。返回 (bytes, content_type, error)。"""
    try:
        resp = _get('/v1/addresses/%s/image' % place_id, params={'width': width}, stream=True)
        if resp.status_code != 200:
            return None, None, 'UPSTREAM_%s' % resp.status_code
        return resp.content, resp.headers.get('Content-Type', 'image/jpeg'), None
    except requests.Timeout:
        return None, None, 'UPSTREAM_TIMEOUT'
    except Exception as e:
        _logger.warning('address image failed: %s', e)
        return None, None, 'UPSTREAM_ERROR'
