"""断点续传判断。"""
from pathlib import Path

MIN_DOWNLOADED_IMAGE_BYTES = 1


def page_already_downloaded(path, ext="webp"):
    """单页是否已完整落盘（文件存在且非空）。"""
    path = Path(path)
    if path.is_file():
        return path.stat().st_size > MIN_DOWNLOADED_IMAGE_BYTES
    # 也容忍同名的其它扩展名图片
    return False


def chapter_already_downloaded(chapter_dir, page_count, ext="webp"):
    """整章是否已完整（从 001 到 page_count 全部落盘且非空）。"""
    chapter_dir = Path(chapter_dir)
    if page_count <= 0:
        return False
    return all(
        page_already_downloaded(chapter_dir / f"{page_no:03d}.{ext}", ext)
        for page_no in range(1, page_count + 1)
    )
