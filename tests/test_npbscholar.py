"""npbscholar データ取得モジュールのテスト（モック使用）。"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from baseball_x.data import npbscholar


SAMPLE_BATTER_JSON = {
    "generated_at": "2026-05-29 09:00:00",
    "columns": [
        {"key": "slug", "label": "Slug", "type": "text"},
        {"key": "player", "label": "Player", "type": "text"},
        {"key": "team", "label": "Team", "type": "text"},
        {"key": "pa", "label": "PA", "type": "number"},
        {"key": "woba", "label": "wOBA", "type": "number"},
        {"key": "xwoba", "label": "xwOBA", "type": "number"},
        {"key": "barrel_pct", "label": "Barrel%", "type": "number"},
    ],
    "rows": [
        {"slug": "p1", "player": "Aさん", "team": "巨人", "pa": 200, "woba": 0.410, "xwoba": 0.395, "barrel_pct": 12.3},
        {"slug": "p2", "player": "Bさん", "team": "阪神", "pa": 180, "woba": 0.380, "xwoba": 0.402, "barrel_pct": 14.1},
    ],
}

SAMPLE_PITCHER_JSON = {
    "generated_at": "2026-05-29 09:00:00",
    "columns": [
        {"key": "slug", "label": "Slug", "type": "text"},
        {"key": "player", "label": "Player", "type": "text"},
        {"key": "team", "label": "Team", "type": "text"},
        {"key": "ip", "label": "IP", "type": "number"},
        {"key": "stuff_plus", "label": "Stuff+", "type": "number"},
        {"key": "location_plus", "label": "Location+", "type": "number"},
        {"key": "k_pct", "label": "K%", "type": "number"},
    ],
    "rows": [
        {"slug": "x1", "player": "Cさん", "team": "DeNA", "ip": 55.0, "stuff_plus": 118.0, "location_plus": 105.0, "k_pct": 28.4},
    ],
}


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict, *_, **__):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url: str):
        return _FakeResponse(self._payload)


def test_fetch_batter_leaderboard_returns_dataframe():
    with patch.object(npbscholar.httpx, "Client", lambda *a, **kw: _FakeClient(SAMPLE_BATTER_JSON)):
        df = npbscholar.fetch_batter_leaderboard(season=2026)
    assert isinstance(df, pd.DataFrame)
    assert {"slug", "player", "team", "woba", "xwoba", "barrel_pct"}.issubset(df.columns)
    assert len(df) == 2
    assert df.attrs["role"] == "batter"
    assert df.attrs["season"] == 2026


def test_fetch_pitcher_leaderboard_returns_dataframe():
    with patch.object(npbscholar.httpx, "Client", lambda *a, **kw: _FakeClient(SAMPLE_PITCHER_JSON)):
        df = npbscholar.fetch_pitcher_leaderboard(season=2026)
    assert {"stuff_plus", "location_plus", "k_pct"}.issubset(df.columns)
    assert df.attrs["role"] == "pitcher"


def test_build_url_switches_between_current_and_past_season():
    current = npbscholar.CURRENT_SEASON
    assert npbscholar._build_url("batter_leaderboard_v1.json", current).endswith(
        "/batter_leaderboard_v1.json"
    )
    past_url = npbscholar._build_url("batter_leaderboard_v1.json", current - 1)
    assert f"/season/{current - 1}/" in past_url


def test_invalid_payload_raises():
    bad_payload = {"unexpected": []}
    with patch.object(npbscholar.httpx, "Client", lambda *a, **kw: _FakeClient(bad_payload)):
        with pytest.raises(ValueError):
            npbscholar.fetch_batter_leaderboard(season=2026)
