"""Pure-Python HeavyKeeper: accurate, memory-bounded top-k frequent items in a data stream."""

from importlib.metadata import PackageNotFoundError, version

from topk._topk import TopK

try:
    __version__ = version("heavykeeper-topk")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"

__all__ = ["TopK", "__version__"]
