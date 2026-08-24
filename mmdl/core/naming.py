"""文件名规范化工具。"""
import re


def clean_name(s):
    """把非法文件名字符替换为空格，收尾去空白，空则 'unknown'。"""
    s = re.sub(r'[\\/:*?"<>|\r\n]', " ", s)
    return re.sub(r"\s+", " ", s).strip() or "unknown"


def natural_sort_key(value):
    """自然排序 key（数字段按数值，字母段按小写）。"""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", str(value))]


def _num(s):
    """从字符串抽第一个数字，无则 0。"""
    m = re.search(r"\d+", s)
    return int(m.group()) if m else 0
