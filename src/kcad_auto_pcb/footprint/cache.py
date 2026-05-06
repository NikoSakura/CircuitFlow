from __future__ import annotations
from collections import OrderedDict
from typing import Optional
from .parser import ResolvedFootprint, FootprintParser


class FootprintCache:
    """LRU cache for footprint geometry data."""

    def __init__(self, max_size: int = 256):
        self._parser = FootprintParser()
        self._cache: OrderedDict[str, ResolvedFootprint] = OrderedDict()
        self._max_size = max_size

    def get(self, name: str) -> Optional[ResolvedFootprint]:
        if name in self._cache:
            self._cache.move_to_end(name)
            return self._cache[name]
        fp = self._parser.resolve(name)
        if fp:
            self._set(name, fp)
        return fp

    def _set(self, name: str, fp: ResolvedFootprint):
        if len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)
        self._cache[name] = fp
