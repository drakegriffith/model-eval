import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from validation import (
    Field,
    Schema,
    ValidationResult,
    validate_record,
    validate_record_all,
    validate_batch,
)


def product_schema():
    return Schema(
        fields=[
            Field(name="sku", required=True, type_=str),
            Field(name="name", required=True, type_=str),
            Field(name="price", required=True, type_=int, min_value=0, max_value=10000),
            Field(
                name="category",
                required=True,
                type_=str,
                choices=["widget", "gadget", "tool"],
            ),
        ]
    )


def valid_record(**overrides):
    record = {
        "sku": "SKU-001",
        "name": "Widget",
        "price": 500,
        "category": "widget",
    }
    record.update(overrides)
    return record


# --- Pre-existing contract: validate_record (fail-fast) -------------------


def test_valid_record_passes():
    result = validate_record(valid_record(), product_schema())
    assert result.is_valid is True
    assert result.errors == []


def test_required_field_missing():
    record = valid_record()
    del record["name"]
    result = validate_record(record, product_schema())
    assert result.is_valid is False
    assert result.errors == ["field 'name' is required"]


def test_wrong_type():
    result = validate_record(valid_record(price="expensive"), product_schema())
    assert result.is_valid is False
    assert result.errors == ["field 'price' must be of type int"]


def test_out_of_range_numeric():
    result = validate_record(valid_record(price=99999), product_schema())
    assert result.is_valid is False
    assert result.errors == ["field 'price' must be <= 10000"]


def test_invalid_choice():
    result = validate_record(valid_record(category="bogus"), product_schema())
    assert result.is_valid is False
    assert result.errors == ["field 'category' must be one of ['widget', 'gadget', 'tool']"]


def test_validate_record_is_fail_fast_with_one_error():
    """Even when multiple fields are invalid, validate_record must stop at
    the first one and return exactly one error."""
    record = valid_record(price="expensive", category="bogus")
    del record["name"]
    result = validate_record(record, product_schema())
    assert result.is_valid is False
    assert len(result.errors) == 1
    # "name" comes before "price" and "category" in schema field order.
    assert result.errors == ["field 'name' is required"]


# --- New ticket behavior: validate_record_all ------------------------------


def test_validate_record_all_collects_every_error():
    record = valid_record(price="expensive", category="bogus")
    del record["name"]
    result = validate_record_all(record, product_schema())
    assert result.is_valid is False
    assert len(result.errors) == 3
    assert result.errors == [
        "field 'name' is required",
        "field 'price' must be of type int",
        "field 'category' must be one of ['widget', 'gadget', 'tool']",
    ]


def test_validate_record_all_valid_record_passes():
    result = validate_record_all(valid_record(), product_schema())
    assert result.is_valid is True
    assert result.errors == []


def test_validate_record_all_single_error_still_invalid():
    record = valid_record()
    del record["name"]
    result = validate_record_all(record, product_schema())
    assert result.is_valid is False
    assert result.errors == ["field 'name' is required"]


# --- New ticket behavior: validate_batch -----------------------------------


def test_validate_batch_no_unique_fields_matches_validate_record_all():
    records = [
        valid_record(sku="SKU-001"),
        valid_record(sku="SKU-002", price="bad"),
    ]
    schema = product_schema()
    batch_results = validate_batch(records, schema)
    independent_results = [validate_record_all(r, schema) for r in records]
    assert [r.errors for r in batch_results] == [r.errors for r in independent_results]
    assert [r.is_valid for r in batch_results] == [r.is_valid for r in independent_results]


def test_validate_batch_flags_duplicate_on_both_records():
    records = [
        valid_record(sku="SKU-DUP", name="First"),
        valid_record(sku="SKU-DUP", name="Second"),
    ]
    results = validate_batch(records, product_schema(), unique_fields=["sku"])
    assert len(results) == 2
    for result in results:
        assert result.is_valid is False
        assert "duplicate value for 'sku': 'SKU-DUP'" in result.errors


def test_validate_batch_individually_valid_record_still_invalid_due_to_duplicate():
    records = [
        valid_record(sku="SKU-DUP", name="First"),
        valid_record(sku="SKU-DUP", name="Second"),
    ]
    results = validate_batch(records, product_schema(), unique_fields=["sku"])
    # Both records are individually valid apart from the collision, so the
    # only error present should be the duplicate-value error.
    for result in results:
        assert result.errors == ["duplicate value for 'sku': 'SKU-DUP'"]


def test_validate_batch_no_duplicates_is_all_valid():
    records = [
        valid_record(sku="SKU-001"),
        valid_record(sku="SKU-002"),
    ]
    results = validate_batch(records, product_schema(), unique_fields=["sku"])
    assert all(r.is_valid for r in results)
    assert all(r.errors == [] for r in results)


def test_validate_batch_missing_unique_values_never_collide():
    records = [
        valid_record(sku="SKU-001"),
        valid_record(sku="SKU-001"),
    ]
    del records[0]["sku"]
    del records[1]["sku"]
    results = validate_batch(records, product_schema(), unique_fields=["sku"])
    # Both records are missing sku (a required field), but the missing
    # values must never be treated as duplicates of each other.
    for result in results:
        assert result.errors == ["field 'sku' is required"]


def test_validate_batch_does_not_mutate_input():
    records = [
        valid_record(sku="SKU-DUP"),
        valid_record(sku="SKU-DUP"),
    ]
    import copy

    original = copy.deepcopy(records)
    validate_batch(records, product_schema(), unique_fields=["sku"])
    assert records == original
