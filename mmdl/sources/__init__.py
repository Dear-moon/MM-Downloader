"""source 注册表。新源在此登记，CLI 与测试都从 get_source 取。"""
from . import mangamillion
# 延后源占位（注册但未实现，capability 未开启）
# from . import bookwalker
# from . import bilibili

SOURCES = {
    "mangamillion": mangamillion.MCMillion,
}


def get_source(name: str):
    cls = SOURCES.get(name)
    if cls is None:
        raise ValueError(f"unknown source: {name!r}")
    return cls()
