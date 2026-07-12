"""兼容入口；推荐使用 ``python -m grade_monitor``。"""

from grade_monitor.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
