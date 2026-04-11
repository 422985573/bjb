# -*- coding: utf-8 -*-
import importlib
import sys


def test_app_import_survives_db_init_failure(monkeypatch):
    monkeypatch.setenv('SECRET_KEY', 'test-secret')
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-admin')
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('DB_PATH', '/dev/null/forbidden.db')

    sys.modules.pop('app', None)
    module = importlib.import_module('app')

    assert module.app is not None
    assert module.app.secret_key == 'test-secret'


def test_gunicorn_conf_handles_bare_log_filenames(monkeypatch):
    monkeypatch.setenv('GUNICORN_ACCESSLOG', 'gunicorn_access.log')
    monkeypatch.setenv('GUNICORN_ERRORLOG', 'gunicorn_error.log')

    sys.modules.pop('gunicorn_conf', None)
    module = importlib.import_module('gunicorn_conf')

    assert module.accesslog == 'gunicorn_access.log'
    assert module.errorlog == 'gunicorn_error.log'


def test_app_import_does_not_require_article_export_dependencies(monkeypatch):
    monkeypatch.setenv('SECRET_KEY', 'test-secret')
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-admin')
    monkeypatch.setenv('APP_ENV', 'development')

    real_import_module = importlib.import_module

    def fake_import_module(name, package=None):
        if name == 'services.article_export':
            raise ImportError('simulated missing export dependency')
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, 'import_module', fake_import_module)
    sys.modules.pop('app', None)
    module = importlib.import_module('app')

    assert module.app is not None


def test_export_route_returns_503_when_export_service_unavailable(monkeypatch):
    from routes import api as api_module

    monkeypatch.setattr(
        api_module,
        '_load_article_export_service',
        lambda: (None, 'ARTICLE_EXPORT_UNAVAILABLE'),
    )

    from app import app
    app.config['TESTING'] = True
    with app.test_client() as client:
        response = client.get('/api/article/not-found/export-xlsx')

    assert response.status_code == 503
