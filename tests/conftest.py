# -*- coding: utf-8 -*-
import os
import tempfile

import pytest


@pytest.fixture(scope='session')
def app_config():
    """测试用配置：临时目录与内存/临时 DB"""
    from dotenv import load_dotenv
    load_dotenv()
    return None


@pytest.fixture
def client(app_config):
    """Flask 测试客户端"""
    os.environ.setdefault('SECRET_KEY', 'test-secret')
    os.environ.setdefault('ADMIN_PASSWORD', 'test-admin')
    try:
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as c:
            yield c
    finally:
        pass
