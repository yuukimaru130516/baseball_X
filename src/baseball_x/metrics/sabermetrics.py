"""指標を扱う補助ユーティリティ。

npbscholar.com の JSON が wOBA / xwOBA / FIP / xFIP / Stuff+ / Location+ 等を
事前計算済みで提供しているため、自前の指標計算は行わない。
ここでは「複数指標を統一スケールで比較する」「サンプル不足を除外する」用の
ヘルパーだけを提供する。
"""
from __future__ import annotations

import pandas as pd


def filter_qualified(df: pd.DataFrame, column: str, min_value: float) -> pd.DataFrame:
    """指定カラムが min_value 以上の行のみ返す。

    例: filter_qualified(batter_df, "pa", 50) で 50打席以上の選手だけを抽出。
    """
    if column not in df.columns:
        return df
    return df[df[column].fillna(0) >= min_value].copy()


def z_score(series: pd.Series) -> pd.Series:
    """系列の z-score（平均0・分散1の標準化値）を返す。"""
    s = series.astype(float)
    mean = s.mean(skipna=True)
    std = s.std(skipna=True, ddof=0)
    if not std or pd.isna(std):
        return pd.Series([0.0] * len(s), index=s.index)
    return (s - mean) / std


def add_diff_column(df: pd.DataFrame, col_a: str, col_b: str, out: str) -> pd.DataFrame:
    """col_a - col_b の差分カラムを追加して返す（コピー）。"""
    result = df.copy()
    if col_a in df.columns and col_b in df.columns:
        result[out] = df[col_a] - df[col_b]
    return result
