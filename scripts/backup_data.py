# -*- coding: utf-8 -*-
"""定时备份：把整个 data/ 目录打包成带时间戳的 tar.gz，并自动清理过期备份。

为什么：月度参数/价格表等数据都存在 data/ 下的 json 与 sqlite 里，一旦误删或写坏
（见 warehouse-settings「静默丢弃」那类 bug）没有备份就无法恢复。此脚本非破坏、只读
data/，可安全反复运行。

用法（服务器项目根目录）：
    ./bjb_venv/bin/python scripts/backup_data.py
    # 或宝塔「计划任务」→ 类型选「Shell 脚本」，命令填上面这行，周期按需（如每天凌晨 3 点）。

可选环境变量：
    BACKUP_DIR    备份输出目录，默认 <项目根>/backups（放在 data/ 之外，避免自我嵌套）
    BACKUP_KEEP   保留最近多少份，默认 30，超出按时间删旧；设 0 表示不清理

退出码：成功 0，失败非 0（宝塔任务日志可据此报警）。
"""
import os
import sys
import tarfile
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
BACKUP_DIR = os.environ.get('BACKUP_DIR') or os.path.join(BASE_DIR, 'backups')
try:
    BACKUP_KEEP = int(os.environ.get('BACKUP_KEEP', '30'))
except ValueError:
    BACKUP_KEEP = 30

PREFIX = 'data_backup_'
SUFFIX = '.tar.gz'


def human_size(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return '%.1f%s' % (n, unit)
        n /= 1024.0


def make_backup():
    if not os.path.isdir(DATA_DIR):
        print('[备份] 找不到 data 目录：%s' % DATA_DIR, file=sys.stderr)
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(BACKUP_DIR, PREFIX + stamp + SUFFIX)
    # 先写临时文件，成功后再改名，避免中断留下半截包被误当成有效备份
    tmp_path = out_path + '.partial'
    try:
        with tarfile.open(tmp_path, 'w:gz') as tar:
            # arcname=data → 解包后是 data/ 目录，路径清晰
            tar.add(DATA_DIR, arcname='data')
        os.replace(tmp_path, out_path)
    except Exception as e:  # noqa: BLE001  打包失败要清掉半截包并上报
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        print('[备份] 打包失败：%s' % e, file=sys.stderr)
        return None
    size = os.path.getsize(out_path)
    print('[备份] 已生成 %s（%s）' % (out_path, human_size(size)))
    return out_path


def prune_old():
    """按文件名时间戳保留最近 BACKUP_KEEP 份，删掉更旧的。"""
    if BACKUP_KEEP <= 0:
        return
    try:
        names = [n for n in os.listdir(BACKUP_DIR)
                 if n.startswith(PREFIX) and n.endswith(SUFFIX)]
    except OSError:
        return
    names.sort()  # 时间戳格式可直接字典序排序，旧的在前
    old = names[:-BACKUP_KEEP] if len(names) > BACKUP_KEEP else []
    for n in old:
        p = os.path.join(BACKUP_DIR, n)
        try:
            os.remove(p)
            print('[备份] 清理过期：%s' % n)
        except OSError as e:
            print('[备份] 清理失败 %s：%s' % (n, e), file=sys.stderr)


def main():
    out = make_backup()
    if not out:
        return 1
    prune_old()
    return 0


if __name__ == '__main__':
    sys.exit(main())
