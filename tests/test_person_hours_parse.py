import json

import pytest

import person_hours as ph
from helpers import GOOD_ESTIMATE


def test_system_prompt_mentions_partial_sessions():
    assert "This may be one day of a longer session" in ph.SYSTEM_PROMPT


def test_parse_estimate_accepts_plain_json():
    est, err = ph.parse_estimate(json.dumps(GOOD_ESTIMATE))
    assert err is None
    assert est == {"summary": "Fixed the duplicate month bug.", "role": "Software engineer",
                   "hours_low": 2.0, "hours_likely": 3.0, "hours_high": 5.0,
                   "rationale": "Tracing plus testing."}


@pytest.mark.parametrize("wrapper", ["```json\nEST\n```", "Here you go:\nEST\nThanks"])
def test_parse_estimate_tolerates_fences_and_prose(wrapper):
    est, err = ph.parse_estimate(wrapper.replace("EST", json.dumps(GOOD_ESTIMATE)))
    assert err is None and est["hours_likely"] == 3.0


@pytest.mark.parametrize("change, expected", [
    ({"hours_low": 4}, "out of order"),
    ({"hours_high": 600}, "out of order"),
    ({"hours_low": 0}, "out of order"),
    ({"hours_likely": "three"}, "non-numeric"),
    ({"summary": " "}, "summary"),
])
def test_parse_estimate_rejects_bad_values(change, expected):
    est, err = ph.parse_estimate(json.dumps({**GOOD_ESTIMATE, **change}))
    assert est is None and expected in err


def test_parse_estimate_rejects_missing_fields_and_non_json():
    missing = {k: v for k, v in GOOD_ESTIMATE.items() if k != "hours_high"}
    assert "non-numeric" in ph.parse_estimate(json.dumps(missing))[1]
    assert ph.parse_estimate("I can't help with that.")[1] == "no JSON object in response"
    assert ph.parse_estimate("{not json}")[1] == "invalid JSON"
