"""下書き生成ジョブ。

npbscholar.com からリーダーボード JSON を取得し、Stuff+/xwOBA 等の
指標ベースで投稿候補を生成して Notion に下書きとして INSERT する。

GitHub Actions schedule: 毎日 07:00 JST
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
from loguru import logger

from baseball_x.data.npbscholar import fetch_batter_leaderboard, fetch_pitcher_leaderboard
from baseball_x.generator.llm_client import generate_draft
from baseball_x.generator.prompts import LEADERBOARD_TEMPLATE, METRIC_DESCRIPTIONS
from baseball_x.metrics.sabermetrics import add_diff_column, filter_qualified
from baseball_x.metrics.selector import score_candidates
from baseball_x.notion.client import insert_draft
from baseball_x.visualization.charts import ranking_bar, scatter_two_metrics

TODAY = date.today()
SEASON = TODAY.year

# 規定サンプル
MIN_PA = 100
MIN_IP = 20.0

# 1日の最大投稿候補数
MAX_DRAFTS = 2


@dataclass
class Candidate:
    category: str
    metric: str
    metric_label: str
    summary_df: pd.DataFrame  # 上位N行（表示・LLM用）
    chart_df: pd.DataFrame    # グラフ生成用（要 name/team 列）
    chart_type: str           # "ranking_bar" or "scatter"
    chart_kwargs: dict = field(default_factory=dict)
    player_slug: str | None = None
    percentile: int = 90

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "metric": self.metric,
            "metric_label": self.metric_label,
            "player_slug": self.player_slug,
            "percentile": self.percentile,
        }


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """charts.py 用に列名を統一する（player→name, team_short→team）。"""
    result = df.copy()
    if "player" in result.columns and "name" not in result.columns:
        result = result.rename(columns={"player": "name"})
    if "team_short" in result.columns:
        result["team"] = result["team_short"]
    return result


def _top_ranking_candidate(
    df: pd.DataFrame,
    *,
    metric_col: str,
    metric_label: str,
    category: str,
    ascending: bool = False,
    top_n: int = 10,
) -> Candidate | None:
    """指標カラムでソートしたランキング型の候補を生成する。

    Args:
        ascending: True なら下位を選ぶ（Whiff%/Chase% 等のコンタクト指標）。
    """
    if metric_col not in df.columns or df[metric_col].notna().sum() < top_n:
        return None
    sorted_df = df.dropna(subset=[metric_col]).sort_values(metric_col, ascending=ascending)
    top = sorted_df.head(top_n)
    display_cols = [c for c in ("player", "team_short", metric_col) if c in top.columns]
    summary = top[display_cols].rename(columns={"team_short": "team", "player": "name"})
    return Candidate(
        category=category,
        metric=metric_label,
        metric_label=metric_label,
        summary_df=summary,
        chart_df=_normalize(sorted_df.iloc[: max(top_n, 20)] if ascending else sorted_df.head(max(top_n, 20))),
        chart_type="ranking_bar",
        chart_kwargs={"metric_col": metric_col, "ascending": ascending, "top_n": top_n},
        player_slug=str(top.iloc[0].get("player", "")),
        percentile=10 if ascending else 90,
    )


def build_pitcher_candidates(pitcher_df: pd.DataFrame) -> list[Candidate]:
    qualified = filter_qualified(pitcher_df, "ip", MIN_IP)
    if qualified.empty:
        logger.warning("No qualified pitchers (IP >= %.1f)", MIN_IP)
        return []

    candidates: list[Candidate] = []
    spec = [
        ("stuff_plus", "Stuff+", "セイバー"),
        ("location_plus", "Location+", "セイバー"),
        ("k_minus_bb_pct", "K-BB%", "セイバー"),
        ("csw_pct", "CSW%", "セイバー"),
    ]
    for col, label, category in spec:
        c = _top_ranking_candidate(
            qualified, metric_col=col, metric_label=label, category=category
        )
        if c:
            candidates.append(c)

    # Stuff+ × Location+ 散布図
    if {"stuff_plus", "location_plus"}.issubset(qualified.columns):
        df_n = _normalize(qualified.dropna(subset=["stuff_plus", "location_plus"]))
        top5 = df_n.nlargest(5, "stuff_plus")[["name", "team", "stuff_plus", "location_plus"]]
        candidates.append(Candidate(
            category="比較",
            metric="Stuff+",
            metric_label="Stuff+ × Location+",
            summary_df=top5,
            chart_df=df_n,
            chart_type="scatter",
            chart_kwargs={"x_col": "stuff_plus", "y_col": "location_plus",
                          "x_label": "Stuff+", "y_label": "Location+"},
            player_slug=str(top5.iloc[0]["name"]) if not top5.empty else None,
            percentile=95,
        ))

    return candidates


def build_batter_candidates(batter_df: pd.DataFrame) -> list[Candidate]:
    qualified = filter_qualified(batter_df, "pa", MIN_PA)
    if qualified.empty:
        logger.warning("No qualified batters (PA >= %d)", MIN_PA)
        return []

    candidates: list[Candidate] = []
    spec = [
        ("xwoba", "xwOBA", "セイバー", False),
        ("barrel_pct", "Barrel%", "セイバー", False),
        ("hard_hit_pct", "HardHit%", "セイバー", False),
        ("whiff_pct", "Whiff%", "セイバー", True),    # 低い順
        ("chase_pct", "Chase%", "セイバー", True),    # 低い順
    ]
    for col, label, category, ascending in spec:
        c = _top_ranking_candidate(
            qualified, metric_col=col, metric_label=label, category=category, ascending=ascending
        )
        if c:
            candidates.append(c)

    # wOBA - xwOBA 乖離（運の良し悪し）
    if {"woba", "xwoba"}.issubset(qualified.columns):
        diff_df = add_diff_column(qualified, "woba", "xwoba", out="woba_diff")
        diff_df = diff_df.dropna(subset=["woba_diff"])
        if not diff_df.empty:
            top = diff_df.nlargest(10, "woba_diff")
            summary = top[["player", "team_short", "woba", "xwoba", "woba_diff"]].rename(
                columns={"team_short": "team", "player": "name"}
            )
            candidates.append(Candidate(
                category="セイバー",
                metric="wOBA-xwOBA",
                metric_label="wOBA−xwOBA（運の上振れ）",
                summary_df=summary,
                chart_df=_normalize(diff_df),
                chart_type="ranking_bar",
                chart_kwargs={"metric_col": "woba_diff", "ascending": False, "top_n": 10},
                player_slug=str(top.iloc[0]["player"]) if not top.empty else None,
                percentile=90,
            ))

    return candidates


def render_chart(cand: Candidate, tmpdir: Path, index: int) -> Path | None:
    """候補からグラフ画像を生成する。失敗時は None。"""
    out = tmpdir / f"chart_{index}.png"
    try:
        title = f"{SEASON} {cand.metric_label}（{TODAY} 時点 / NPB Scholar）"
        if cand.chart_type == "ranking_bar":
            return ranking_bar(
                df=cand.chart_df,
                metric_col=cand.chart_kwargs["metric_col"],
                metric_label=cand.metric_label,
                title=title,
                output_path=out,
                top_n=cand.chart_kwargs.get("top_n", 10),
            )
        if cand.chart_type == "scatter":
            return scatter_two_metrics(
                df=cand.chart_df,
                x_col=cand.chart_kwargs["x_col"],
                y_col=cand.chart_kwargs["y_col"],
                x_label=cand.chart_kwargs["x_label"],
                y_label=cand.chart_kwargs["y_label"],
                title=title,
                output_path=out,
            )
    except Exception:
        logger.exception("Chart generation failed for %s", cand.metric_label)
    return None


def generate_and_insert(cand: Candidate, image_path: Path | None) -> None:
    """1件の候補について本文を生成して Notion に登録する。"""
    data_summary = cand.summary_df.to_string(index=False)
    prompt = LEADERBOARD_TEMPLATE.format(
        metric_name=cand.metric_label,
        metric_description=METRIC_DESCRIPTIONS.get(cand.metric, ""),
        data_summary=data_summary,
        recent_topics="（履歴なし）",
    )
    body = generate_draft(prompt)
    logger.info(f"Generated draft ({len(body)}字): {body[:50]}...")

    insert_draft(
        body=body,
        category=cand.category,
        source_data=data_summary,
        image_url=str(image_path) if image_path else None,
        metric=cand.metric,
        player_slug=cand.player_slug,
    )


def main() -> None:
    logger.info(f"Generator job started: date={TODAY}")

    pitcher_df = fetch_pitcher_leaderboard(season=SEASON)
    batter_df = fetch_batter_leaderboard(season=SEASON)

    candidates = build_pitcher_candidates(pitcher_df) + build_batter_candidates(batter_df)
    if not candidates:
        logger.warning("No candidates produced. Aborting.")
        return

    # score_candidates は dict を期待するので変換 → 結果を Candidate にマッピングし直す
    cand_dicts = [c.to_dict() for c in candidates]
    ranked_dicts = score_candidates(cand_dicts)
    cand_by_key = {(c.metric, c.player_slug): c for c in candidates}
    ranked: list[Candidate] = []
    for d in ranked_dicts:
        key = (d["metric"], d.get("player_slug"))
        if key in cand_by_key:
            ranked.append(cand_by_key[key])

    top = ranked[:MAX_DRAFTS]
    logger.info(f"Selected {len(top)}/{len(candidates)} candidates to post")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        for i, cand in enumerate(top):
            image_path = render_chart(cand, tmppath, i)
            generate_and_insert(cand, image_path)

    logger.info("Generator job completed")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Generator job failed")
        sys.exit(1)
