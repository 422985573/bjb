# -*- coding: utf-8 -*-
"""应用配置：从环境变量与 .env 加载"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SECRET_KEY = os.environ.get('SECRET_KEY') or 'quote_website_secret_key_2026'
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or '999888777a'
DB_PATH = os.environ.get('DB_PATH') or os.path.join(_BASE_DIR, 'data', 'database.db')
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER') or os.path.join(_BASE_DIR, 'uploads')
POSTCODE_CSV_PATH = os.environ.get('POSTCODE_CSV_PATH') or os.path.join(_BASE_DIR, 'data', 'postcode_distance_final.csv')
# DG 整柜参考 xlsx（config.DG_QUOTE_XLSX_PATH）；柜类 Excel 模板清单见 services/dg_excel_template_registry.py
DG_QUOTE_XLSX_PATH = os.environ.get('DG_QUOTE_XLSX_PATH') or os.path.join(_BASE_DIR, '4月25日DG周鹏.xlsx')
# 新大货文章 border/toll 偏远附加费源表（scripts/init_remote_surcharge.py 据此建库）
BORDER_REMOTE_XLSX = os.environ.get('BORDER_REMOTE_XLSX') or os.path.join(_BASE_DIR, 'border偏远费.xlsx')
TOLL_REMOTE_XLSX = os.environ.get('TOLL_REMOTE_XLSX') or os.path.join(_BASE_DIR, 'TOLL偏远费.xlsx')
PER_PAGE = int(os.environ.get('PER_PAGE', '20'))
APP_ENV = (os.environ.get('APP_ENV') or os.environ.get('FLASK_ENV') or 'development').strip().lower()
DEBUG = (os.environ.get('DEBUG') or os.environ.get('FLASK_DEBUG') or '').strip() in {'1', 'true', 'True'}
CHANNEL_REJECT_POSTCODE_FILES = {'纸箱': '纸箱.txt', '大件': '大件.txt'}
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# 第三方地址接口（autocomplete/detail/image）——顶部搜索框支持输入地址时使用。
# 生产环境建议把 ADDRESS_API_KEY 放到环境变量 / .env，勿依赖此默认值。
ADDRESS_API_BASE = (os.environ.get('ADDRESS_API_BASE') or 'https://api.aub.56llm.com').rstrip('/')
ADDRESS_API_KEY = os.environ.get('ADDRESS_API_KEY') or 'd18471ff96f0c1aa99b51d43dc2afff4549108927799e201'
ADDRESS_API_TIMEOUT = float(os.environ.get('ADDRESS_API_TIMEOUT') or '10')


def validate_runtime_config():
    """生产环境禁止使用默认密钥/口令，避免误配置上线。"""
    if APP_ENV not in {'prod', 'production'}:
        return
    if SECRET_KEY == 'quote_website_secret_key_2026':
        raise RuntimeError('生产环境必须配置 SECRET_KEY')
    if ADMIN_PASSWORD == '999888777a':
        raise RuntimeError('生产环境必须配置 ADMIN_PASSWORD')
