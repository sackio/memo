"""Memo model + per-class field requirements. [001/FR-001 001/FR-002 001/FR-004 001/FR-005 001/FR-006 001/FR-007 001/FR-008 001/FR-009]

Covers every value of the `class` taxonomy (FR-001) and each special-field
requirement in data-model.md §Validation Rules.

Marker set is WIDER than task T023 specified. T023 listed FR-001/005/006/007/
008/009, but the Phase 2 gate requires FR-001..FR-009 to be FULL, and FR-004
(provenance) + FR-002 (bi-temporal window) had implementation anchors with no
enforcing test anywhere in the phase — so the gate failed FR-004 as PARTIAL.
Both are genuinely exercised here (provenance construction/alias handling, and
the valid_from <= valid_until rule), so they are anchored rather than left
uncovered. See the T023 note in tasks.md.

Two rules from that section are intentionally NOT asserted here, because they
are not the model's job and the corresponding tests live elsewhere:
  * `class = fact` requires provenance — owned by the storage mediator, which
    can reclassify to legacy-unattributed instead of rejecting (Phase 3).
  * `derived_from` ids must exist — needs a DB round-trip, so it is a write-time
    check, not a pure-model one.
"""
import pytest
from pydantic import ValidationError

from memo.models import (
    ConstitutionMeta,
    Memo,
    MemoClass,
    Provenance,
    Reopenability,
    TimeScope,
)

# Every class in the FR-001 taxonomy. Kept as an explicit tuple rather than
# derived from the Literal so that ADDING a class to the model without
# considering its field requirements makes test_taxonomy_is_exhaustive fail.
ALL_CLASSES = (
    "constitutional",
    "behavioral",
    "goal",
    "verbatim-critical",
    "fact",
    "decision-in-progress",
    "episodic",
    "ephemeral-flush",
    "time-scoped",
    "legacy-unattributed",
)

NOW = 1_700_000_000.0


def make_memo(**overrides):
    """Minimal valid Memo, overridable per test."""
    base = {
        "id": "11111111-1111-4111-8111-111111111111",
        "content": "body",
        "created_at": NOW,
        "updated_at": NOW,
        "valid_from": NOW,
    }
    base.update(overrides)
    return Memo(**base)


def test_taxonomy_is_exhaustive():
    """ALL_CLASSES must match the model's Literal exactly.

    Guards the rest of this file: if a class is added to MemoClass and not to
    ALL_CLASSES, the per-class coverage below would silently skip it.
    """
    from typing import get_args

    assert set(get_args(MemoClass)) == set(ALL_CLASSES)


# --- FR-001: class taxonomy ---

@pytest.mark.parametrize("cls", ALL_CLASSES)
def test_every_class_is_constructible(cls):
    """Each class constructs once its own required special fields are supplied."""
    extra = {}
    if cls == "constitutional":
        extra["injection_mode"] = "forcible-constitutional"
        extra["constitution_meta"] = ConstitutionMeta(
            version="1.3.0", ratified_at=NOW, amended_at=NOW, incident_ref="ref"
        )
    elif cls == "time-scoped":
        extra["time_scope"] = TimeScope(start=NOW, end=NOW + 3600)
    elif cls == "ephemeral-flush":
        extra["expires_at"] = NOW + 600

    memo = make_memo(**{"class": cls}, **extra)
    assert memo.class_ == cls


def test_default_class_is_fact():
    assert make_memo().class_ == "fact"


def test_unknown_class_rejected():
    with pytest.raises(ValidationError):
        make_memo(**{"class": "not-a-real-class"})


# --- FR-006: injection_mode ---

@pytest.mark.parametrize("mode", [
    "forcible-current-focus", "on-recall", "on-procedure-match",
])
def test_injection_modes_accepted(mode):
    assert make_memo(injection_mode=mode).injection_mode == mode


def test_default_injection_mode_is_on_recall():
    assert make_memo().injection_mode == "on-recall"


def test_unknown_injection_mode_rejected():
    with pytest.raises(ValidationError):
        make_memo(injection_mode="forcible-everything")


# --- data-model.md: class=constitutional requires constitution_meta + mode ---

def test_constitutional_requires_constitution_meta():
    with pytest.raises(ValidationError, match="constitution_meta"):
        make_memo(**{"class": "constitutional"},
                  injection_mode="forcible-constitutional")


def test_constitutional_requires_forcible_injection_mode():
    with pytest.raises(ValidationError, match="forcible-constitutional"):
        make_memo(
            **{"class": "constitutional"},
            injection_mode="on-recall",
            constitution_meta=ConstitutionMeta(
                version="1.3.0", ratified_at=NOW, amended_at=NOW, incident_ref="ref"
            ),
        )


def test_constitution_meta_rejected_on_non_constitutional_class():
    """data-model.md: constitution_meta is required on constitutional, "else NULL"."""
    with pytest.raises(ValidationError, match="only valid on class=constitutional"):
        make_memo(
            **{"class": "behavioral"},
            constitution_meta=ConstitutionMeta(
                version="1.3.0", ratified_at=NOW, amended_at=NOW, incident_ref="ref"
            ),
        )


# --- FR-005: class=time-scoped requires time_scope ---

def test_time_scoped_requires_time_scope():
    with pytest.raises(ValidationError, match="time_scope"):
        make_memo(**{"class": "time-scoped"})


def test_time_scope_optional_fields():
    ts = TimeScope(start=NOW, end=NOW + 60, trip_id="trip-1",
                   calendar_event_id="evt-1")
    memo = make_memo(**{"class": "time-scoped"}, time_scope=ts)
    assert memo.time_scope.trip_id == "trip-1"
    assert memo.time_scope.calendar_event_id == "evt-1"


# --- FR-007: class=ephemeral-flush requires expires_at ---

def test_ephemeral_flush_requires_expires_at():
    with pytest.raises(ValidationError, match="expires_at"):
        make_memo(**{"class": "ephemeral-flush"})


def test_expires_at_allowed_on_other_classes():
    """TTL is "primarily" ephemeral-flush, not exclusively — must not be blocked."""
    assert make_memo(**{"class": "episodic"}, expires_at=NOW + 60).expires_at == NOW + 60


# --- FR-009: reopenability on decision-in-progress ---

def test_decision_in_progress_reopenability_is_optional():
    """Nullable per C-04 — presence must not be required."""
    assert make_memo(**{"class": "decision-in-progress"}).reopenability is None


def test_decision_in_progress_accepts_reopenability():
    memo = make_memo(
        **{"class": "decision-in-progress"},
        reopenability=Reopenability(
            challenge_if_delays_build_by_days=14,
            operator_tempo_hint="ben ships fast; challenge early",
        ),
    )
    assert memo.reopenability.challenge_if_delays_build_by_days == 14


# --- FR-008: scope ---

def test_scope_defaults_to_global():
    assert make_memo().scope == ["global"]


@pytest.mark.parametrize("scope", [
    ["global"],
    ["project:memo"],
    ["session:mcp-session-test"],
    ["agent-family:genomics"],
    ["project:memo", "agent-family:genomics"],
])
def test_scope_accepts_documented_forms(scope):
    assert make_memo(scope=scope).scope == scope


# --- FR-002: bi-temporal window validation ---

def test_valid_until_may_be_null():
    assert make_memo(valid_until=None).valid_until is None


def test_valid_until_equal_to_valid_from_is_allowed():
    """Zero-length window: a memo superseded the instant it was written."""
    assert make_memo(valid_until=NOW).valid_until == NOW


def test_valid_until_before_valid_from_rejected():
    with pytest.raises(ValidationError, match="valid_until must be >= valid_from"):
        make_memo(valid_until=NOW - 1)


# --- FR-004: provenance + alias handling ---

def test_provenance_accepts_partial_block():
    memo = make_memo(provenance=Provenance(gmail_msg_id="msg-123"))
    assert memo.provenance.gmail_msg_id == "msg-123"
    assert memo.provenance.git_ref is None


def test_class_alias_round_trips():
    """`class` is a Python keyword, so the field is class_ with alias "class".

    by_alias=True must emit "class" — the DB column name and the wire name.
    """
    memo = make_memo(**{"class": "episodic"})
    dumped = memo.model_dump(by_alias=True)
    assert dumped["class"] == "episodic"
    assert "class_" not in dumped


def test_class_populatable_by_field_name():
    """populate_by_name=True — internal callers may pass class_ directly."""
    assert make_memo(class_="goal").class_ == "goal"
