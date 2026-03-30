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
POSTCODE_API_BASE_URL = (os.environ.get('POSTCODE_API_BASE_URL') or 'http://py.api.aubaws.xyz').rstrip('/')
POSTCODE_CSV_PATH = os.environ.get('POSTCODE_CSV_PATH') or os.path.join(_BASE_DIR, 'data', 'postcode_distance_final.csv')
PER_PAGE = int(os.environ.get('PER_PAGE', '20'))
APP_ENV = (os.environ.get('APP_ENV') or os.environ.get('FLASK_ENV') or 'development').strip().lower()
DEBUG = (os.environ.get('DEBUG') or os.environ.get('FLASK_DEBUG') or '').strip() in {'1', 'true', 'True'}
CHANNEL_REJECT_POSTCODE_FILES = {'纸箱': '纸箱.txt', '大件': '大件.txt'}
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def validate_runtime_config():
    """生产环境禁止使用默认密钥/口令，避免误配置上线。"""
    if APP_ENV not in {'prod', 'production'}:
        return
    if SECRET_KEY == 'quote_website_secret_key_2026':
        raise RuntimeError('生产环境必须配置 SECRET_KEY')
    if ADMIN_PASSWORD == '999888777a':
        raise RuntimeError('生产环境必须配置 ADMIN_PASSWORD')
