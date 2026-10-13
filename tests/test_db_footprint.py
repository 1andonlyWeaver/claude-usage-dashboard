import math
from datetime import date, timedelta

import pytest

import db
from helpers import add_message


def _ts(days_ago, hhmm="10:00"):
    return f"{(date.today() - timedelta(days=days_ago)).isoformat()}T{hhmm}:00"


def _row(input=0, cache_5m=0, cache_1h=0, cache_read=0, output=0):
    return {"input_tokens": input, "cache_5m_tokens": cache_5m, "cache_1h_tokens": cache_1h,
            "cache_read_tokens": cache_read, "output_tokens": output}


@pytest.mark.parametrize("model, pricing", [
    ("claude-opus-4-1-20250805", db.OPUS_4_PRICING),
    ("claude-opus-4-20250514", db.OPUS_4_PRICING),       # the snapshot date isn't a version
    ("claude-opus-5-5", db.OPUS_5_5_PRICING),
    ("claude-3-5-haiku-20241022", db.HAIKU_4_5_PRICING),  # no parseable version: newest tier
    ("us.anthropic.claude-sonnet-4-5", db.SONNET_4_PRICING),
    ("claude-fable-5-1", db.FABLE_5_1_PRICING),
    ("unknown", db.DEFAULT_PRICING),
    ("<synthetic>", db.DEFAULT_PRICING),
])
def test_price_for_model_is_unchanged_by_the_shared_parser(model, pricing):
    assert db.price_for_model(model) == pricing


def test_parse_model():
    assert db._parse_model("claude-opus-4-5-20251101") == ("opus", (4, 5))
    assert db._parse_model("claude-sonnet-5") == ("sonnet", (5, 0))
    assert db._parse_model("claude-3-5-haiku-20241022") == ("haiku", None)
    assert db._parse_model("<synthetic>") == (None, None)
    assert db._parse_model(None) == (None, None)


def test_group_energy_per_token_type():
    f = db.FOOTPRINT_WH_PER_1K
    for kind, row in [("input", _row(input=1000)), ("cache_write", _row(cache_5m=600, cache_1h=400)),
                      ("cache_read", _row(cache_read=1000)), ("output", _row(output=1000))]:
        assert db._group_energy("claude-sonnet-5", row) == pytest.approx(f[kind]), kind


def test_group_energy_scales_by_family():
    row = _row(input=2000, cache_read=50_000, output=3000)
    sonnet = db._group_energy("claude-sonnet-5", row)
    for model, mult in [("claude-opus-5-5", 2.0), ("claude-haiku-4-5", 0.5), ("claude-fable-5-1", 5.0),
                        ("unknown", 1.0)]:
        assert db._group_energy(model, row) == pytest.approx(tuple(mult * x for x in sonnet)), model


def test_one_million_cache_reads_and_ten_thousand_output_tokens_on_opus():
    # 2 x (1000 x 0.02 + 10 x 1.0) Wh
    assert db._group_energy("claude-opus-5-5", _row(cache_read=1_000_000, output=10_000))[1] == pytest.approx(60.0)


def test_combine():
    assert db._combine((1.0, 2.0, 5.0)) == pytest.approx((1.0, 2.0, 5.0))
    lo, c, hi = db._combine((1.0, 10.0, 100.0), (2.0, 4.0, 8.0))
    assert c == pytest.approx(40.0)
    assert lo == pytest.approx(40.0 * math.exp(-math.hypot(math.log(10), math.log(2))))
    assert hi == pytest.approx(40.0 * math.exp(math.hypot(math.log(10), math.log(2))))
    assert db._combine((0.0, 0.0, 0.0), (2.0, 4.0, 8.0)) == (0.0, 0.0, 0.0)


def test_estimate_footprint_totals(conn):
    add_message(conn, "s1", _ts(1), model="claude-opus-5-5", input_tokens=0,
                cache_read_tokens=1_000_000, output_tokens=10_000)
    add_message(conn, "s2", _ts(2), model="claude-sonnet-5", input_tokens=0,
                cache_read_tokens=0, output_tokens=5_000)
    f = db.estimate_footprint(30)

    assert f["days"] == 30
    assert f["energy_wh"]["central"] == pytest.approx(65.0)
    assert [r["model"] for r in f["breakdown"]] == ["claude-opus-5-5", "claude-sonnet-5"]
    assert f["breakdown"][0]["energy_wh"] == pytest.approx(60.0)
    assert f["cache_read_share"] == pytest.approx(40.0 / 65.0)

    kwh = 0.065
    assert f["water_cooling_l"] == pytest.approx(kwh * db.WATER_COOLING_L_PER_KWH[1])
    assert f["water_generation_l"] == pytest.approx(kwh * db.WATER_GENERATION_L_PER_KWH[1])
    assert f["water_l"]["central"] == pytest.approx(f["water_cooling_l"] + f["water_generation_l"])
    assert f["co2e_g"]["central"] == pytest.approx(kwh * db.CO2E_KG_PER_KWH[1] * 1000)
    for metric in ("energy_wh", "water_l", "co2e_g"):
        r = f[metric]
        assert 0 < r["low"] < r["central"] < r["high"], metric

    c = f["comparisons"]
    assert c["home_days"] == pytest.approx(kwh / db.HOME_KWH_PER_DAY)
    assert c["showers"] == pytest.approx(f["water_l"]["central"] / db.SHOWER_L)
    assert c["shower_minutes"] == pytest.approx(c["showers"] * db.SHOWER_MINUTES)
    assert c["car_miles"] == pytest.approx(f["co2e_g"]["central"] / db.CAR_G_CO2_PER_MILE)


def test_legacy_cache_creation_counts_as_cache_writes(conn):
    # Ingested before Claude Code logged the 5m/1h split: only cache_creation_tokens is set.
    add_message(conn, "s1", _ts(1), input_tokens=0, output_tokens=0, cache_creation_tokens=1000)
    f = db.estimate_footprint(30)
    assert f["energy_wh"]["central"] == pytest.approx(db.FOOTPRINT_WH_PER_1K["cache_write"][1])


def test_days_filter(conn):
    add_message(conn, "old", _ts(40), input_tokens=0, output_tokens=1000)
    add_message(conn, "new", _ts(1), input_tokens=0, output_tokens=1000)
    assert db.estimate_footprint(30)["energy_wh"]["central"] == pytest.approx(1.0)
    assert db.estimate_footprint(90)["energy_wh"]["central"] == pytest.approx(2.0)


def test_empty_db_gives_zeros(conn):
    f = db.estimate_footprint(30)
    assert f["energy_wh"] == {"low": 0.0, "central": 0.0, "high": 0.0}
    assert f["water_l"] == {"low": 0.0, "central": 0.0, "high": 0.0}
    assert f["co2e_g"] == {"low": 0.0, "central": 0.0, "high": 0.0}
    assert f["breakdown"] == [] and f["cache_read_share"] == 0.0
    assert f["comparisons"]["car_miles"] == 0.0


def test_session_footprint(conn):
    add_message(conn, "s1", _ts(1, "10:00"), model="claude-opus-5-5", input_tokens=0, output_tokens=1000)
    add_message(conn, "s1", _ts(1, "10:05"), model="claude-sonnet-5", input_tokens=0, output_tokens=1000)
    add_message(conn, "s2", _ts(1, "11:00"), output_tokens=5000)
    f = db.session_footprint("s1")
    assert f["energy_wh"]["central"] == pytest.approx(2.0 + 1.0)
    assert {r["model"] for r in f["breakdown"]} == {"claude-opus-5-5", "claude-sonnet-5"}
    assert db.session_footprint("nope") is None
