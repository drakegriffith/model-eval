"""A small record-validation library.

Fields are described by ``Field`` specs, grouped into a ``Schema``, and a
record (a plain ``dict``) is checked against that schema with
``validate_record``.

``validate_record`` is fail-fast: it returns as soon as it finds the first
invalid field, with exactly one message in ``ValidationResult.errors``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Field:
    """A single field spec.

    ``type_`` is the expected Python type of the value (e.g. ``str``,
    ``int``, ``float``). ``min_value``/``max_value`` only apply to numeric
    fields. ``choices``, if given, restricts the value to a fixed set.
    """

    name: str
    required: bool = True
    type_: type = str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    choices: Optional[List[Any]] = None


@dataclass
class Schema:
    """An ordered collection of ``Field`` specs."""

    fields: List[Field] = field(default_factory=list)

    def __iter__(self):
        return iter(self.fields)


@dataclass
class ValidationResult:
    is_valid: bool
    errors: List[str]


def _field_error(f: Field, record: Dict[str, Any]) -> Optional[str]:
    """Return an error message for ``f`` against ``record``, or ``None``."""
    has_value = f.name in record and record[f.name] is not None

    if not has_value:
        if f.required:
            return f"field '{f.name}' is required"
        return None

    value = record[f.name]

    if not isinstance(value, f.type_):
        return f"field '{f.name}' must be of type {f.type_.__name__}"

    if f.min_value is not None and value < f.min_value:
        return f"field '{f.name}' must be >= {f.min_value}"

    if f.max_value is not None and value > f.max_value:
        return f"field '{f.name}' must be <= {f.max_value}"

    if f.choices is not None and value not in f.choices:
        return f"field '{f.name}' must be one of {f.choices}"

    return None


def validate_record(record: Dict[str, Any], schema: Schema) -> ValidationResult:
    """Validate ``record`` against ``schema``, stopping at the first error."""
    for f in schema:
        error = _field_error(f, record)
        if error is not None:
            return ValidationResult(is_valid=False, errors=[error])
    return ValidationResult(is_valid=True, errors=[])
