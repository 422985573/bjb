# -*- coding: utf-8 -*-
def test_config_loads():
    import config
    assert config.SECRET_KEY
    assert config.PER_PAGE >= 1
    assert config.POSTCODE_API_BASE_URL.startswith('http')
