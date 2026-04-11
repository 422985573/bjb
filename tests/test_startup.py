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
