# Copyright (c) 2026 MiniStack Contributors. SPDX-License-Identifier: MIT
"""Unit tests for numeric ``*Path`` Choice operators in the Step Functions engine.

Unlike the integration tests in ``test_stepfunctions.py`` these exercise the
ASL rule evaluator directly, so no running emulator is required.
"""

from ministack.services.stepfunctions import _evaluate_rule


def test_numeric_path_operators_relate_variable_to_resolved_path():
    """NumericLessThanPath/GreaterThanPath/LessThanEqualsPath/GreaterThanEqualsPath
    compare the Variable-resolved LHS against the path-resolved RHS."""
    data = {"value": 5, "limit": 3, "floor": 5, "ceil": 10}
    assert _evaluate_rule({"Variable": "$.value", "NumericLessThanPath": "$.ceil"}, data)
    assert _evaluate_rule({"Variable": "$.value", "NumericGreaterThanPath": "$.limit"}, data)
    assert _evaluate_rule({"Variable": "$.value", "NumericLessThanEqualsPath": "$.floor"}, data)
    assert _evaluate_rule({"Variable": "$.value", "NumericGreaterThanEqualsPath": "$.floor"}, data)
    # Inverse comparisons must not match.
    assert not _evaluate_rule({"Variable": "$.value", "NumericLessThanPath": "$.limit"}, data)
    assert not _evaluate_rule({"Variable": "$.value", "NumericGreaterThanPath": "$.ceil"}, data)
    assert not _evaluate_rule({"Variable": "$.value", "NumericLessThanEqualsPath": "$.limit"}, data)
    assert not _evaluate_rule({"Variable": "$.value", "NumericGreaterThanEqualsPath": "$.ceil"}, data)


def test_numeric_path_operators_reject_bool_operand():
    """Booleans are not numeric operands: ``_is_num`` excludes ``bool`` even though
    ``bool`` is an ``int`` subclass in Python."""
    data = {"flag": True, "limit": 0}
    for op in (
        "NumericEqualsPath",
        "NumericLessThanPath",
        "NumericGreaterThanPath",
        "NumericLessThanEqualsPath",
        "NumericGreaterThanEqualsPath",
    ):
        assert not _evaluate_rule({"Variable": "$.flag", op: "$.limit"}, data), op


def test_numeric_path_operators_reject_non_numeric_operand():
    """Strings and missing values are not numeric operands."""
    data = {"text": "5", "limit": 3}
    for op in (
        "NumericLessThanPath",
        "NumericGreaterThanPath",
        "NumericLessThanEqualsPath",
        "NumericGreaterThanEqualsPath",
    ):
        assert not _evaluate_rule({"Variable": "$.text", op: "$.limit"}, data), op
        assert not _evaluate_rule({"Variable": "$.missing", op: "$.limit"}, data), op


def test_numeric_path_operators_float_operands():
    """Float LHS and RHS values compare correctly."""
    data = {"value": 0.5, "limit": 0.25}
    assert _evaluate_rule({"Variable": "$.value", "NumericGreaterThanPath": "$.limit"}, data)
    assert _evaluate_rule({"Variable": "$.value", "NumericGreaterThanEqualsPath": "$.limit"}, data)
    assert not _evaluate_rule({"Variable": "$.value", "NumericLessThanPath": "$.limit"}, data)


def test_numeric_path_operators_reject_non_numeric_rhs():
    """A non-numeric RHS (missing, string, null, or bool) must make the rule False:
    no TypeError from comparing a number to a string and no bool coerced to int
    (e.g. ``1 >= True`` must not match)."""
    cases = [
        ("missing", {}),
        ("text", {"text": "3"}),
        ("null", {"null": None}),
        ("flag", {"flag": True}),
    ]
    for op in (
        "NumericLessThanPath",
        "NumericGreaterThanPath",
        "NumericLessThanEqualsPath",
        "NumericGreaterThanEqualsPath",
    ):
        for rhs_key, data in cases:
            data = {**data, "value": 1}
            # "1 >= True" is True under raw Python int coercion; the guarded
            # comparison must return False instead.
            assert not _evaluate_rule({"Variable": "$.value", op: f"$.{rhs_key}"}, data), (
                op,
                rhs_key,
            )


def test_numeric_path_operators_accept_int_float_rhs_mix():
    """int LHS vs float RHS (and vice versa) still compares numerically."""
    assert _evaluate_rule(
        {"Variable": "$.value", "NumericLessThanPath": "$.limit"}, {"value": 1, "limit": 1.5}
    )
    assert _evaluate_rule(
        {"Variable": "$.value", "NumericGreaterThanEqualsPath": "$.limit"}, {"value": 2.0, "limit": 2}
    )


def test_numeric_equals_path_still_works():
    """Regression guard for the pre-existing NumericEqualsPath operator."""
    data = {"value": 7, "expected": 7}
    assert _evaluate_rule({"Variable": "$.value", "NumericEqualsPath": "$.expected"}, data)
    assert not _evaluate_rule({"Variable": "$.value", "NumericEqualsPath": "$.other"}, {"value": 7, "other": 8})
