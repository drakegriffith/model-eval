import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from catalog import Catalog, Item


def make_items(n, category="widget", price=100):
    return [
        Item(sku=f"SKU-{i:03d}", name=f"Item {i:03d}", price_cents=price + i, category=category)
        for i in range(n)
    ]


def test_add_and_all():
    cat = Catalog()
    cat.add(Item(sku="A1", name="Alpha", price_cents=500, category="tools"))
    assert len(cat) == 1
    assert cat.all()[0].sku == "A1"


def test_add_many():
    cat = Catalog()
    cat.add_many(make_items(5))
    assert len(cat) == 5


def test_filter_by_category():
    cat = Catalog()
    cat.add_many(make_items(3, category="tools"))
    cat.add_many(make_items(2, category="toys"))
    tools = cat.filter(category="tools")
    assert len(tools) == 3
    assert all(i.category == "tools" for i in tools)


def test_filter_in_stock_only():
    cat = Catalog()
    cat.add(Item(sku="S1", name="In", price_cents=100, category="a", in_stock=True))
    cat.add(Item(sku="S2", name="Out", price_cents=100, category="a", in_stock=False))
    result = cat.filter(in_stock_only=True)
    assert len(result) == 1
    assert result[0].sku == "S1"


def test_filter_max_price():
    cat = Catalog()
    cat.add_many(make_items(5, price=100))  # prices 100..104
    result = cat.filter(max_price_cents=102)
    assert len(result) == 3


def test_sort_by_price():
    cat = Catalog()
    cat.add(Item(sku="B", name="B", price_cents=300, category="a"))
    cat.add(Item(sku="A", name="A", price_cents=100, category="a"))
    ordered = cat.sort(cat.all(), key="price")
    assert [i.sku for i in ordered] == ["A", "B"]


def test_sort_by_name_reverse():
    cat = Catalog()
    cat.add(Item(sku="A", name="Apple", price_cents=1, category="a"))
    cat.add(Item(sku="B", name="Banana", price_cents=1, category="a"))
    ordered = cat.sort(cat.all(), key="name", reverse=True)
    assert [i.sku for i in ordered] == ["B", "A"]


def test_sort_unknown_key_raises():
    cat = Catalog()
    cat.add_many(make_items(2))
    with pytest.raises(ValueError):
        cat.sort(cat.all(), key="bogus")


def test_paginate_first_page_full():
    cat = Catalog()
    cat.add_many(make_items(10))
    page = cat.paginate(page_number=1, page_size=4)
    assert len(page.items) == 4
    assert page.items[0].sku == "SKU-000"


def test_paginate_exact_multiple_total_pages():
    cat = Catalog()
    cat.add_many(make_items(10))
    page = cat.paginate(page_number=1, page_size=5)
    assert page.total_pages == 2
    assert page.total_items == 10


def test_paginate_partial_last_page_reachable():
    """10 items at page_size=3 must yield 4 pages (ceil(10/3)), with the
    4th page holding the single leftover item. This is the case a
    floor-division bug drops entirely."""
    cat = Catalog()
    cat.add_many(make_items(10))
    page = cat.paginate(page_number=1, page_size=3)
    assert page.total_pages == 4

    last_page = cat.paginate(page_number=4, page_size=3)
    assert len(last_page.items) == 1
    assert last_page.items[0].sku == "SKU-009"


def test_paginate_small_catalog_single_page():
    """A catalog smaller than one page must still report exactly one
    reachable page, not zero."""
    cat = Catalog()
    cat.add_many(make_items(5))
    page = cat.paginate(page_number=1, page_size=20)
    assert page.total_pages == 1
    assert len(page.items) == 5


def test_paginate_page_beyond_total_is_empty():
    cat = Catalog()
    cat.add_many(make_items(10))
    page = cat.paginate(page_number=99, page_size=3)
    assert page.items == []


def test_paginate_empty_catalog():
    cat = Catalog()
    page = cat.paginate(page_number=1, page_size=10)
    assert page.total_pages == 0
    assert page.items == []


def test_has_next_and_has_prev():
    cat = Catalog()
    cat.add_many(make_items(9))
    first = cat.paginate(page_number=1, page_size=3)
    middle = cat.paginate(page_number=2, page_size=3)
    last = cat.paginate(page_number=3, page_size=3)
    assert first.has_next is True
    assert first.has_prev is False
    assert middle.has_next is True
    assert middle.has_prev is True
    assert last.has_next is False
    assert last.has_prev is True


def test_paginate_invalid_page_size_raises():
    cat = Catalog()
    with pytest.raises(ValueError):
        cat.paginate(page_number=1, page_size=0)


def test_paginate_invalid_page_number_raises():
    cat = Catalog()
    with pytest.raises(ValueError):
        cat.paginate(page_number=0, page_size=10)


def test_filter_then_sort_then_paginate_pipeline():
    cat = Catalog()
    cat.add_many(make_items(7, category="tools", price=50))
    cat.add_many(make_items(3, category="toys", price=10))
    filtered = cat.filter(category="tools")
    ordered = cat.sort(filtered, key="price", reverse=True)
    page = cat.paginate(items=ordered, page_number=1, page_size=5)
    assert page.total_items == 7
    assert page.total_pages == 2
    assert page.items[0].price_cents == max(i.price_cents for i in filtered)
