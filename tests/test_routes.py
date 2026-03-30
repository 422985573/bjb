# -*- coding: utf-8 -*-
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
