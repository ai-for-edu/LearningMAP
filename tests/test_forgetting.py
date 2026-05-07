"""Forgetting model contract + EbbinghausV1 default."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from src.forgetting import ForgettingModel, get_forgetting_model
from src.forgetting.ebbinghaus import EbbinghausV1


UTC = timezone.utc


class TestEbbinghausV1:
    def setup_method(self) -> None:
        self.model = EbbinghausV1()
        self.now = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)

    def test_unseen_topic_returns_zero(self) -> None:
        # last_accuracy is None → never tested → no retention to speak of
        r = self.model.retention(
            last_seen=self.now - timedelta(days=1),
            last_accuracy=None,
            session_count=0,
            now=self.now,
        )
        assert r == 0.0

    def test_just_seen_perfect_score_high_retention(self) -> None:
        r = self.model.retention(
            last_seen=self.now - timedelta(hours=1),
            last_accuracy=1.0,
            session_count=1,
            now=self.now,
        )
        # ~1 hour after a perfect first study: should be near 1
        assert r > 0.95

    def test_decay_over_time(self) -> None:
        r1 = self.model.retention(
            last_seen=self.now - timedelta(days=1),
            last_accuracy=1.0, session_count=1, now=self.now,
        )
        r7 = self.model.retention(
            last_seen=self.now - timedelta(days=7),
            last_accuracy=1.0, session_count=1, now=self.now,
        )
        r30 = self.model.retention(
            last_seen=self.now - timedelta(days=30),
            last_accuracy=1.0, session_count=1, now=self.now,
        )
        assert r1 > r7 > r30

    def test_better_accuracy_better_retention(self) -> None:
        kw = dict(last_seen=self.now - timedelta(days=3), session_count=1, now=self.now)
        r_low = self.model.retention(last_accuracy=0.2, **kw)
        r_high = self.model.retention(last_accuracy=1.0, **kw)
        assert r_high > r_low

    def test_more_repetitions_better_retention(self) -> None:
        kw = dict(
            last_seen=self.now - timedelta(days=3),
            last_accuracy=0.8, now=self.now,
        )
        r_once = self.model.retention(session_count=1, **kw)
        r_many = self.model.retention(session_count=5, **kw)
        assert r_many > r_once

    def test_future_last_seen_treated_as_zero_age(self) -> None:
        # Clock-skew / stale-timestamp safety: future last_seen yields
        # retention=1.0 (treated as just-seen), not a >1 overflow.
        r = self.model.retention(
            last_seen=self.now + timedelta(days=1),
            last_accuracy=1.0, session_count=1, now=self.now,
        )
        assert r == 1.0

    def test_name_attribute(self) -> None:
        assert self.model.name == "ebbinghaus_v1"

    def test_formula_exact(self) -> None:
        # Pin one concrete value so a refactor that changes the formula
        # is caught immediately.
        # accuracy=0.5, session_count=1, days=2:
        #   S = 1.0 + 4.0*0.5 + 1.0*(1-1) = 3.0
        #   R = exp(-2/3) ≈ 0.5134
        r = self.model.retention(
            last_seen=self.now - timedelta(days=2),
            last_accuracy=0.5, session_count=1, now=self.now,
        )
        assert math.isclose(r, math.exp(-2.0 / 3.0), rel_tol=1e-6)


class TestRegistry:
    def test_default_returns_ebbinghaus(self) -> None:
        m = get_forgetting_model()
        assert m.name == "ebbinghaus_v1"

    def test_named_lookup(self) -> None:
        m = get_forgetting_model("ebbinghaus_v1")
        assert isinstance(m, EbbinghausV1)

    def test_unknown_name_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown forgetting model"):
            get_forgetting_model("does_not_exist")


class TestProtocolConformance:
    def test_ebbinghaus_satisfies_protocol(self) -> None:
        # @runtime_checkable on ForgettingModel makes this a structural check.
        assert isinstance(EbbinghausV1(), ForgettingModel)

    def test_factory_returns_protocol_instance(self) -> None:
        assert isinstance(get_forgetting_model(), ForgettingModel)
