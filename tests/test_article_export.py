# -*- coding: utf-8 -*-
from services.article_export import QUICK_LINKS, build_article_workbook


def test_build_article_workbook_includes_quick_links():
    article = {'title': '示例文章', 'category_name': '示例分类'}
    workbook = build_article_workbook(article=article, modules=[], exported_at_text='2026-04-11 10:00:00')
    sheet = workbook.active

    assert sheet['A4'].value == '快捷链接'

    for offset, (label, url) in enumerate(QUICK_LINKS, start=5):
        cell = sheet[f'A{offset}']
        assert cell.value == label
        assert cell.hyperlink is not None
        assert cell.hyperlink.target == url
