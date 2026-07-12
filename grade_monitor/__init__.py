"""北京信息科技大学教务系统成绩监控。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("find-score")
except PackageNotFoundError:
    # 直接从未安装的源码目录运行时没有包元数据。
    __version__ = "0+unknown"

__all__ = ["__version__"]
