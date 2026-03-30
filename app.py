# -*- coding: utf-8 -*-
"""应用入口：创建 Flask 应用、加载配置、注册蓝图与过滤器"""
from flask import Flask, send_from_directory, jsonify

import config
import db
import filters
from routes.front import front_bp
from routes.admin import admin_bp
from routes.api import api_bp

config.validate_runtime_config()

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config['UPLOAD_FOLDER'] = config.UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

import os  # noqa: E402
os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)

filters.register_filters(app)
db.init_db()

app.register_blueprint(front_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(api_bp)


@app.route('/health')
def health():
    """健康检查，供负载均衡或容器探针使用"""
    try:
        with db.get_db() as conn:
            conn.cursor().execute('SELECT 1').fetchone()
        return jsonify({'status': 'ok', 'db': 'ok'}), 200
    except Exception:
        return jsonify({'status': 'degraded', 'db': 'error'}), 503


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """访问上传的文件"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# 同一上传/图片列表接口同时挂到 /admin/api 下（编辑器使用）
from routes.api import upload as api_upload_view  # noqa: E402
from routes.api import uploaded_images as api_uploaded_images_view  # noqa: E402
app.add_url_rule('/admin/api/upload', view_func=api_upload_view, methods=['POST'])
app.add_url_rule('/admin/api/uploaded-images', view_func=api_uploaded_images_view, methods=['GET'])


if __name__ == '__main__':
    app.run(debug=config.DEBUG, host='0.0.0.0', port=5001)
