# -*- coding: utf-8 -*-
"""
Excel 栅格模板清单（当前：9类电池柜报价表、普柜报价表）。二者共用同一编辑器 UI、前台 article 栅格版式与交互逻辑；与 dg_grid 中非 schema v2 的模块对应。

新增模板时：
1. 在此追加一条 EXCEL_QUOTE_TEMPLATE_ENTRIES（含 source_xlsx、label）；
2. 将 xlsx 置于项目根目录（或与种子脚本约定路径）；
3. 后台「添加模块」需在 editor.html / addModule 中增加对应入口（template_id + 默认 title）。
"""
from __future__ import annotations

from typing import Any, Tuple

# id：稳定键（可与分类名 / 文件名 stem 一致）；source_xlsx：相对项目根的路径
EXCEL_QUOTE_TEMPLATE_ENTRIES: Tuple[dict[str, Any], ...] = (
    {"id": "9类电池柜", "source_xlsx": "9类电池柜.xlsx", "label": "9类电池柜报价表"},
    {"id": "普柜", "source_xlsx": "普柜.xlsx", "label": "普柜报价表"},
)

DEFAULT_SEED_FILES: Tuple[str, ...] = tuple(e["source_xlsx"] for e in EXCEL_QUOTE_TEMPLATE_ENTRIES)
