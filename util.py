# -*- coding: utf-8 -*-
"""通用工具：管理员鉴权装饰器 + 文件数据的原子写与跨进程写锁。

系统把海外仓/小包报价等数据直接写到 JSON 文件，且线上是多进程多线程（gunicorn
workers×threads）。为「绝不丢数据」，所有落盘一律走 atomic_write_json（写临时文件→
fsync→os.replace 原子替换，并保留上一版 .bak），所有「读→改→写」再叠加 data_dir_lock
（fcntl.flock 跨进程串行）避免并发互相覆盖。
"""
import json
import os
import shutil
import threading
from contextlib import contextmanager
from functools import wraps

from flask import session, request, jsonify, redirect, url_for

try:
    import fcntl  # POSIX（线上 Linux / 本地 macOS 均可用）
except ImportError:  # pragma: no cover - Windows 无 fcntl，降级为无锁（本项目不部署 Windows）
    fcntl = None

# 记录当前线程已持有的目录锁深度，实现同线程可重入（见 data_dir_lock）
_LOCK_STATE = threading.local()


def atomic_write_json(path, obj, *, indent=2, compact=False):
    """原子写 JSON：要么完整写入新内容，要么保持旧文件不变，绝不产生半截/空文件。

    步骤：先序列化（出错则抛异常、根本不碰磁盘）→ 写同目录临时文件并 fsync →
    若目标已存在先备份成 <path>.bak（保留上一版可人工恢复）→ os.replace 原子替换。
    任一步失败都会清理临时文件并向上抛异常，交由调用方返回错误、旧数据保持完好。
    """
    if compact:
        text = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    else:
        text = json.dumps(obj, ensure_ascii=False, indent=indent)

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp_path = path + '.tmp'
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # 替换前先把当前好文件留一份 .bak（尽力而为，失败不影响主流程）
        if os.path.isfile(path):
            try:
                shutil.copy2(path, path + '.bak')
            except OSError:
                pass
        os.replace(tmp_path, path)
    except BaseException:
        # 出错清理临时文件，绝不留下垃圾；异常继续上抛
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise
    # 尽力 fsync 目录，保证 rename 落盘（部分文件系统需要）
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


@contextmanager
def data_dir_lock(dir_path):
    """对某数据目录加跨进程排他写锁（fcntl.flock），串行化该目录内的所有「读→改→写」。

    锁文件为 <dir>/.write.lock（不以 .json 结尾、不参与业务扫描）。多进程/多线程同时
    保存时排队执行，避免 lost update。fcntl 不可用时降级为无操作（保证功能不阻断）。

    **同线程可重入**：同一线程已持有某目录锁时，内层再申请只是空进出（用 thread-local
    深度计数），避免「同进程两个 fd 各自 flock 同一文件」互相阻塞造成的自死锁。
    """
    if fcntl is None:
        yield
        return
    os.makedirs(dir_path, exist_ok=True)
    key = os.path.abspath(dir_path)
    held = getattr(_LOCK_STATE, 'depth', None)
    if held is None:
        held = _LOCK_STATE.depth = {}
    if held.get(key, 0) > 0:
        # 本线程已持有该目录锁 → 重入，直接执行，不重复 flock
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    lock_path = os.path.join(dir_path, '.write.lock')
    lock_file = open(lock_path, 'a+')
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        held[key] = 1
        yield
    finally:
        held[key] = held.get(key, 1) - 1
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def admin_required(f):
    """管理员登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            if request.path.startswith('/api/') or request.is_json or (
                request.accept_mimetypes.best and 'application/json' in str(request.accept_mimetypes)
            ):
                return jsonify({'success': False, 'message': '请先登录或会话已过期'}), 401
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated_function
