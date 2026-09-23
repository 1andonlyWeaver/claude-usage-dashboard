import json

import pytest

import person_hours as ph
from helpers import GOOD_ESTIMATE


def test_system_prompt_mentions_partial_sessions():
    assert "This may be one day of a longer session" in ph.SYSTEM_PROMPT
    assert "not work to count" in ph.SYSTEM_PROMPT


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


def test_parse_estimate_takes_the_first_object():
    reply = "Thinking {aside}\n" + json.dumps(GOOD_ESTIMATE) + "\nNote: {this} is extra."
    est, err = ph.parse_estimate(reply)
    assert err is None and est["hours_likely"] == 3.0


def test_parse_estimate_rejects_booleans_and_numeric_strings():
    for bad in (True, "3"):
        est, err = ph.parse_estimate(json.dumps({**GOOD_ESTIMATE, "hours_likely": bad}))
        assert est is None and "non-numeric" in err


def test_parse_estimate_never_raises_on_extreme_input():
    huge = '{"summary": "x", "hours_low": 1, "hours_likely": 1' + "0" * 400 + ', "hours_high": 5}'
    assert ph.parse_estimate(huge)[0] is None
    assert ph.parse_estimate('{"a": ' * 5000)[0] is None
    newline = '{"summary": "a\nb", "role": "", "hours_low": 1, "hours_likely": 2, "hours_high": 3}'
    assert ph.parse_estimate(newline)[1] is None


def test_parse_estimate_clips_long_fields():
    est, _ = ph.parse_estimate(json.dumps({**GOOD_ESTIMATE, "summary": "s" * 5000,
                                           "role": "r" * 500, "rationale": "w" * 5000}))
    assert len(est["summary"]) <= ph.MAX_SUMMARY_CHARS
    assert len(est["role"]) <= ph.MAX_ROLE_CHARS
    assert len(est["rationale"]) <= ph.MAX_RATIONALE_CHARS
