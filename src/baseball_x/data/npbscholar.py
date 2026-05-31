"""npbscholar.com のリーダーボード JSON から pandas DataFrame を取得する。

データ提供元: https://npbscholar.com/
- robots.txt: User-agent: * / Allow: /
- 利用上の注意: Content-Signal で ai-train=no が明示されている。
  本モジュールは「閲覧・引用」目的のデータ取得であり、AI 学習には用いない。

エンドポイント:
- 現シーズン   : {BASE}/{role}_leaderboard_v1.json
- 過去シーズン : {BASE}/season/{year}/{role}_leaderboard_v1.json
"""
from __future__ import annotations

import time
from datetime import date

import httpx
import pandas as pd
from loguru import logger

BASE_URL = "https://npbscholar.com/data"
CURRENT_SEASON = date.today().year

USER_AGENT = "baseball-x-bot/0.1 (NPB stats summarizer for X posts; source: NPB Scholar)"
TIMEOUT_SECONDS = 10.0
MAX_RETRIES = 3
MIN_EXPECTED_ROWS = 50  # これを下回ったら警告（規定打席未到達のオフ期等を除く）

PITCHER_FILE = "pitcher_leaderboard_v1.json"
BATTER_FILE = "batter_leaderboard_v1.json"


def _build_url(filename: str, season: int) -> str:
    if season == CURRENT_SEASON:
        return f"{BASE_URL}/{filename}"
    return f"{BASE_URL}/season/{season}/{filename}"


def _fetch_json(url: str) -> dict:
    """指定 URL から JSON を取得する。失敗時は指数バックオフで最大 MAX_RETRIES 回リトライ。"""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(headers=headers, timeout=TIMEOUT_SECONDS, follow_redirects=True) as c:
                resp = c.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            last_error = e
            wait = 2 ** (attempt - 1)
            logger.warning(f"Fetch failed ({url}) attempt={attempt}/{MAX_RETRIES}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts: {last_error}")


def _parse_ip(value: object) -> float | None:
    """投球回表記 "49.2"（49回2/3）を真の小数イニング 49.667 へ換算する。

    小数第1位はアウト数（0/1/2）を表す野球独自表記。欠損・不正値は None。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        whole_str, _, frac_str = text.partition(".")
        whole = int(whole_str) if whole_str else 0
        outs = int(frac_str[0]) if frac_str else 0
        return whole + outs / 3
    except (ValueError, IndexError):
        return None


def _to_dataframe(payload: dict, *, role: str, season: int) -> pd.DataFrame:
    """npbscholar の {generated_at, columns, rows} 形式 JSON を DataFrame に変換する。"""
    rows = payload.get("rows")
    columns = payload.get("columns")
    if not isinstance(rows, list) or not isinstance(columns, list):
        raise ValueError(f"Unexpected JSON shape for {role} (season={season}): {list(payload.keys())}")

    df = pd.DataFrame(rows)

    # JSON は数値も文字列（"49.2" 等）で返すため、columns メタデータの type を
    # 手がかりに数値列を数値へ変換する。変換できない値は NaN に落とす。
    # type=="ip" は投球回表記（"49.2" = 49回2/3）なので真の小数イニングへ換算する。
    for col in columns:
        if not isinstance(col, dict):
            continue
        key = col.get("key")
        if key not in df.columns:
            continue
        col_type = col.get("type")
        if col_type == "number":
            df[key] = pd.to_numeric(df[key], errors="coerce")
        elif col_type == "ip":
            df[key] = df[key].map(_parse_ip)

    df.attrs["generated_at"] = payload.get("generated_at")
    df.attrs["season"] = season
    df.attrs["role"] = role

    if len(df) < MIN_EXPECTED_ROWS:
        logger.warning(
            f"{role} leaderboard returned only {len(df)} rows (season={season}). "
            "Off-season or schema change?"
        )
    return df


def fetch_pitcher_leaderboard(season: int = CURRENT_SEASON) -> pd.DataFrame:
    """投手リーダーボードを DataFrame で返す。

    主なカラム:
        slug, player, name_romaji, team, team_code, team_short, team_display,
        g, gs, w, l, sv, ip, h, r, er, hr, bb, so,
        pitching_run_value, fastball_run_value, breaking_run_value, offspeed_run_value,
        stuff_plus, location_plus, fb_velo, k_pct, bb_pct, k_minus_bb_pct, csw_pct, ...
    """
    url = _build_url(PITCHER_FILE, season)
    logger.info(f"Fetching pitcher leaderboard: {url}")
    payload = _fetch_json(url)
    df = _to_dataframe(payload, role="pitcher", season=season)
    logger.info(f"  → {len(df)} pitchers (generated_at={df.attrs.get('generated_at')})")
    return df


def fetch_batter_leaderboard(season: int = CURRENT_SEASON) -> pd.DataFrame:
    """打者リーダーボードを DataFrame で返す。

    主なカラム:
        slug, player, name_romaji, team, team_code, team_short, team_display,
        pa, avg, obp, slg, ops, woba, xwoba, xwoba_minus_woba,
        xba, xba_minus_ba, xslg, xslg_minus_slg,
        hard_hit_pct, barrel_pct, whiff_pct, chase_pct, gb_pct, sweet_spot_pct,
        hits, singles, doubles, triples, home_runs, ...
    """
    url = _build_url(BATTER_FILE, season)
    logger.info(f"Fetching batter leaderboard: {url}")
    payload = _fetch_json(url)
    df = _to_dataframe(payload, role="batter", season=season)
    logger.info(f"  → {len(df)} batters (generated_at={df.attrs.get('generated_at')})")
    return df
