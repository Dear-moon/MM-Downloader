#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""向后兼容 shim：所有逻辑已移到 mmdl 包。本文件仅供 CLI 入口，测试请勿 patch 它。"""
import sys

from mmdl.cli import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
