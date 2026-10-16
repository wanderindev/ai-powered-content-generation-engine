"""seed_local_db binds json/jsonb values explicitly (lists included)."""

from psycopg2.extras import Json
from scripts.seed_local_db import row_params


def test_row_params_wraps_json_lists_and_dicts_but_not_other_columns():
    row = {
        "id": 1,
        "tags": ["a", "b"],  # TEXT[] — must stay a list
        "audio_manifest_es": [{"key": "k", "duration_ms": 1}],
        "fixed_price_config": {"price": 10},
        "audio_qa_es": None,
    }
    cols = list(row)
    json_cols = {"audio_manifest_es", "fixed_price_config", "audio_qa_es"}

    params = row_params(row, cols, json_cols)

    assert params["p0"] == 1
    assert params["p1"] == ["a", "b"]
    assert isinstance(params["p2"], Json)
    assert params["p2"].adapted == [{"key": "k", "duration_ms": 1}]
    assert isinstance(params["p3"], Json)
    assert params["p4"] is None
