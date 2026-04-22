# -*- coding: utf-8 -*-
import db


def test_index(client):
    r = client.get('/')
    assert r.status_code == 200


def test_admin_login_redirect(client):
    r = client.get('/admin/')
    assert r.status_code in (200, 302)


def test_api_postcode_channel_status(client):
    r = client.get('/api/postcode-channel-status')
    assert r.status_code == 200
    data = r.get_json()
    assert data.get('success') is True and 'data' in data


def test_front_only_shows_published_articles(client):
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO categories (name, sort_order) VALUES ('测试分类', 0)")
        category_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO articles (article_code, category_id, title, is_published) VALUES (?, ?, ?, ?)",
            ('T-PUBLISHED', category_id, '已发布文章', 1)
        )
        cursor.execute(
            "INSERT INTO articles (article_code, category_id, title, is_published) VALUES (?, ?, ?, ?)",
            ('T-UNPUBLISHED', category_id, '未发布文章', 0)
        )
        conn.commit()

    r = client.get('/')
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert '已发布文章' in body
    assert '未发布文章' not in body


def test_admin_can_toggle_publish_status(client):
    with client.session_transaction() as session:
        session['admin_logged_in'] = True

    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO categories (name, sort_order) VALUES ('切换分类', 0)")
        category_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO articles (article_code, category_id, title, is_published) VALUES (?, ?, ?, ?)",
            ('T-TOGGLE', category_id, '切换文章', 1)
        )
        article_id = cursor.lastrowid
        conn.commit()

    r = client.post(f'/api/article/{article_id}/publish-status', json={'is_published': 0})
    assert r.status_code == 200
    data = r.get_json()
    assert data.get('success') is True
    assert data.get('is_published') == 0

    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT is_published FROM articles WHERE id = ?', (article_id,))
        row = cursor.fetchone()
        assert row['is_published'] == 0
