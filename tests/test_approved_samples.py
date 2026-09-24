"""The buyer approved these four files; the qualifying rule must keep passing them."""

from pathlib import Path

import pytest

from musescore_midi.roles import qualifies

SAMPLES = sorted((Path(__file__).parent.parent / "approved_samples").glob("*.mid"))


def test_all_four_approved_samples_are_present():
    assert [p.name for p in SAMPLES] == ["sample2.mid", "sample3.mid", "sample4.mid", "sample5.mid"]


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_approved_sample_meets_the_rule(path):
    ok, _ = qualifies(path)
    assert ok
