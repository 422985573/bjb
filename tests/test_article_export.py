# -*- coding: utf-8 -*-
from services.article_export import build_article_workbook


def test_build_article_workbook_has_no_quick_links_header():
    """导出从模块内容起，不含页头与快捷链接区。"""
    article = {'title': '示例文章', 'category_name': '示例分类'}
    workbook = build_article_workbook(article=article, modules=[], exported_at_text='2026-04-11 10:00:00')
    sheet = workbook.active

    assert sheet['A1'].value is None
