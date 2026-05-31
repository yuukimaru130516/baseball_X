"""指標を扱う補助ユーティリティ。

npbscholar.com の JSON が wOBA / xwOBA / FIP / xFIP / Stuff+ / Location+ 等を
事前計算済みで提供しているため、自前の指標計算は行わない。
ここでは「複数指標を統一スケールで比較する」「サンプル不足を除外する」用の
ヘルパーだけを提供する。
"""
from __future__ import annotations

import pandas as pd


# NPB のリーグ区分（team_code ベース）
CENTRAL_CODES = {"G", "T", "C", "D", "S", "DB"}  # 巨人/阪神/広島/中日/ヤクルト/DeNA
PACIFIC_CODES = {"B", "E", "F", "H", "L", "M"}   # オリックス/楽天/日本ハム/SB/西武/ロッテ
LEAGUES = ("セ", "パ")
LEAGUE_FULL = {"セ": "セ・リーグ", "パ": "パ・リーグ"}


def league_of_code(code: object) -> str | None:
    """team_code からリーグ（"セ"/"パ"）を返す。不明は None。"""
    if code in CENTRAL_CODES:
        return "セ"
    if code in PACIFIC_CODES:
        return "パ"
    return None


def add_league_column(df: pd.DataFrame) -> pd.DataFrame:
    """team_code からリーグ列 league（"セ"/"パ"）を付与する（コピーを返す）。"""
    result = df.copy()
    if "team_code" in result.columns:
        result["league"] = result["team_code"].map(league_of_code)
    else:
        result["league"] = None
    return result


def filter_qualified(df: pd.DataFrame, column: str, min_value: float) -> pd.DataFrame:
    """指定カラムが min_value 以上の行のみ返す。

    例: filter_qualified(batter_df, "pa", 50) で 50打席以上の選手だけを抽出。
    """
    if column not in df.columns:
        return df
    return df[df[column].fillna(0) >= min_value].copy()


def filter_qualified_pitcher(
    df: pd.DataFrame, min_ip: float, min_g: int
) -> pd.DataFrame:
    """投手の対象者を「min_ip イニング以上 または min_g 登板以上」で絞り込む。

    先発（イニング基準）と中継ぎ（登板数基準）の両方を公平に拾うための OR 条件。
    """
    ip = pd.to_numeric(df["ip"], errors="coerce") if "ip" in df.columns else None
    g = pd.to_numeric(df["g"], errors="coerce") if "g" in df.columns else None
    if ip is None and g is None:
        return df.copy()
    cond = False
    if ip is not None:
        cond = cond | (ip.fillna(0) >= min_ip)
    if g is not None:
        cond = cond | (g.fillna(0) >= min_g)
    return df[cond].copy()


def z_score(series: pd.Series) -> pd.Series:
    """系列の z-score（平均0・分散1の標準化値）を返す。"""
    s = series.astype(float)
    mean = s.mean(skipna=True)
    std = s.std(skipna=True, ddof=0)
    if not std or pd.isna(std):
        return pd.Series([0.0] * len(s), index=s.index)
    return (s - mean) / std


def percentile_rank(
    series: pd.Series, value: float | None, *, lower_is_better: bool = False
) -> int | None:
    """value が series 内で上位何パーセンタイルかを 0〜100 の整数で返す。

    「その選手が母集団の何%より優れているか」を表す。
    例: 95 を返したら「規定到達者の 95% を上回る」。

    Args:
        lower_is_better: Whiff%/Chase% など値が小さいほど良い指標では True。
    欠損や母集団が空のときは None。
    """
    s = series.dropna().astype(float)
    if len(s) == 0 or value is None or pd.isna(value):
        return None
    better = (s > value).sum() if lower_is_better else (s < value).sum()
    return round(better / len(s) * 100)


def rank_in_league(
    series: pd.Series, value: float | None, *, lower_is_better: bool = False
) -> int | None:
    """value が series 内で何位かを 1 始まりの整数で返す（1位 = 最も優秀）。

    自分より優秀な選手数 + 1。lower_is_better=True では小さい値ほど上位。
    """
    s = series.dropna().astype(float)
    if len(s) == 0 or value is None or pd.isna(value):
        return None
    ahead = (s < value).sum() if lower_is_better else (s > value).sum()
    return int(ahead) + 1


def add_batter_derived_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """カウント系から導出できる打者指標を計算して列を補完する（コピーを返す）。

    npbscholar の打者フィードは Barrel%/HardHit%/Whiff%/Chase% などトラッキング
    （打球速度・角度・1球ごとのスイング）を要する指標が空で提供される。これらは
    集計値から正しく再現できないため計算しない。代わりに安打・三振・本塁打などの
    集計値から計算できる古典的指標を補う:

    - k_pct  : 三振率 = 三振 / 打席 × 100（小さいほど優秀）
    - hr_pct : 本塁打率 = 本塁打 / 打席 × 100
    - iso    : 純長打率 = 長打率 − 打率（大きいほど長打力）
    """
    result = df.copy()
    pa = pd.to_numeric(result.get("pa"), errors="coerce")
    pa = pa.where(pa > 0)  # 0 打席はゼロ除算を避けて NaN に
    if "strikeouts" in result.columns:
        result["k_pct"] = pd.to_numeric(result["strikeouts"], errors="coerce") / pa * 100
    if "home_runs" in result.columns:
        result["hr_pct"] = pd.to_numeric(result["home_runs"], errors="coerce") / pa * 100
    if {"slg", "avg"}.issubset(result.columns):
        result["iso"] = (
            pd.to_numeric(result["slg"], errors="coerce")
            - pd.to_numeric(result["avg"], errors="coerce")
        )
    return result


def add_diff_column(df: pd.DataFrame, col_a: str, col_b: str, out: str) -> pd.DataFrame:
    """col_a - col_b の差分カラムを追加して返す（コピー）。"""
    result = df.copy()
    if col_a in df.columns and col_b in df.columns:
        result[out] = df[col_a] - df[col_b]
    return result
