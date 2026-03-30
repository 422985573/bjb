#!/bin/bash

# 澳洲豹货运代理报价表 - 本地运行脚本

echo "正在启动项目..."

# 获取脚本所在目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# 检查虚拟环境是否存在
if [ ! -d "bjb_venv" ]; then
    echo "错误: 虚拟环境 bjb_venv 不存在"
    echo "请先创建虚拟环境: python3 -m venv bjb_venv"
    exit 1
fi

# 激活虚拟环境
source bjb_venv/bin/activate

# 使用 python3 和 pip3（macOS 兼容性更好）
PYTHON_CMD="python3"
PIP_CMD="pip3"

# 检查 Python 是否可用
if ! command -v $PYTHON_CMD &> /dev/null; then
    echo "错误: Python3 未找到"
    exit 1
fi

# 检查依赖是否安装（不在启动时自动安装，避免环境不可控）
if ! $PYTHON_CMD -c "import flask" 2>/dev/null; then
    echo "错误: 检测到依赖未安装"
    echo "请先手动执行: $PIP_CMD install -r requirements.txt"
    exit 1
fi

# 运行应用
echo "项目启动中..."
echo "访问地址: http://localhost:5001"
echo "管理后台: http://localhost:5001/admin/login"
echo "按 Ctrl+C 停止服务"
echo ""

export APP_ENV="${APP_ENV:-development}"
export DEBUG="${DEBUG:-0}"
$PYTHON_CMD app.py

