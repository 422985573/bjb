# Gunicorn 配置；可通过环境变量覆盖路径相关项
import os

# 项目根目录（默认当前文件所在目录的父目录，或设置 GUNICORN_CHDIR）
_base = os.environ.get('GUNICORN_CHDIR') or os.path.dirname(os.path.abspath(__file__))

chdir = _base

workers = int(os.environ.get('GUNICORN_WORKERS', '4'))
threads = int(os.environ.get('GUNICORN_THREADS', '2'))
worker_class = os.environ.get('GUNICORN_WORKER_CLASS', 'gthread')
if worker_class == 'sync':
    threads = 1

user = os.environ.get('GUNICORN_USER', 'root')
bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:5001')

pidfile = os.environ.get('GUNICORN_PIDFILE') or os.path.join(_base, 'gunicorn.pid')
accesslog = os.environ.get('GUNICORN_ACCESSLOG') or os.path.join(_base, 'logs', 'gunicorn_access.log')
errorlog = os.environ.get('GUNICORN_ERRORLOG') or os.path.join(_base, 'logs', 'gunicorn_error.log')

accesslog_dir = os.path.dirname(accesslog)
errorlog_dir = os.path.dirname(errorlog)
if accesslog_dir:
    os.makedirs(accesslog_dir, exist_ok=True)
if errorlog_dir and errorlog_dir != accesslog_dir:
    os.makedirs(errorlog_dir, exist_ok=True)

loglevel = os.environ.get('GUNICORN_LOGLEVEL', 'info')
