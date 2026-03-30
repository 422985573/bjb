# -*- coding: utf-8 -*-
def test_health_route(client):
    r = client.get('/health')
    assert r.status_code in (200, 503)
    data = r.get_json()
    assert data and 'status' in data
