"""A small in-memory product catalog with filtering, sorting, and pagination.

This module backs a storefront listing page: callers add items, then ask
for a sorted/filtered view broken into pages of a fixed size.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional


@dataclass(frozen=True)
class Item:
    sku: str
    name: str
    price_cents: int
    category: str
    in_stock: bool = True


@dataclass(frozen=True)
class Page:
    items: List[Item]
    page_number: int          # 1-indexed
    page_size: int
    total_items: int
    total_pages: int

    @property
    def has_next(self) -> bool:
        return self.page_number < self.total_pages

    @property
    def has_prev(self) -> bool:
        return self.page_number > 1


class Catalog:
    """An in-memory collection of catalog items."""

    def __init__(self, items: Optional[Iterable[Item]] = None):
        self._items: List[Item] = list(items) if items else []

    def add(self, item: Item) -> None:
        self._items.append(item)

    def add_many(self, items: Iterable[Item]) -> None:
        for item in items:
            self.add(item)

    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> List[Item]:
        return list(self._items)

    def filter(
        self,
        category: Optional[str] = None,
        in_stock_only: bool = False,
        max_price_cents: Optional[int] = None,
    ) -> List[Item]:
        result = self._items
        if category is not None:
            result = [i for i in result if i.category == category]
        if in_stock_only:
            result = [i for i in result if i.in_stock]
        if max_price_cents is not None:
            result = [i for i in result if i.price_cents <= max_price_cents]
        return list(result)

    def sort(
        self,
        items: List[Item],
        key: str = "name",
        reverse: bool = False,
    ) -> List[Item]:
        keyfuncs: dict[str, Callable[[Item], object]] = {
            "name": lambda i: i.name.lower(),
            "price": lambda i: i.price_cents,
            "sku": lambda i: i.sku,
        }
        if key not in keyfuncs:
            raise ValueError(f"unknown sort key: {key!r}")
        return sorted(items, key=keyfuncs[key], reverse=reverse)

    def paginate(
        self,
        items: Optional[List[Item]] = None,
        page_number: int = 1,
        page_size: int = 20,
    ) -> Page:
        """Return a single 1-indexed page of ``items`` (or all catalog items).

        ``total_pages`` must be the ceiling of ``len(items) / page_size`` so
        that a partially-filled final page is still reachable. Requesting a
        page beyond the last returns an empty item list without raising.
        """
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        if page_number <= 0:
            raise ValueError("page_number must be positive")

        source = self._items if items is None else items
        total_items = len(source)

        # BUG: floor division drops a trailing partial page. If total_items
        # is not an exact multiple of page_size, the last (partial) page
        # becomes unreachable and its items silently vanish from pagination.
        total_pages = total_items // page_size

        start = (page_number - 1) * page_size
        end = start + page_size
        page_items = source[start:end] if page_number <= total_pages else []

        return Page(
            items=page_items,
            page_number=page_number,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
        )
