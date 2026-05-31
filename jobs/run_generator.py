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
from baseball_x.generator.prompts import (
    LEADERBOARD_TEMPLATE,
    METRIC_DESCRIPTIONS,
    append_team_hashtag,
)
from baseball_x.metrics.sabermetrics import (
    LEAGUE_FULL,
    LEAGUES,
    add_batter_derived_metrics,
    add_diff_column,
    add_league_column,
    filter_qualified,
    filter_qualified_pitcher,
)
from baseball_x.metrics.selector import score_candidates, select_diverse
from baseball_x.notion.client import insert_draft
from baseball_x.visualization.charts import ranking_bar, scatter_two_metrics

TODAY = date.today()
SEASON = TODAY.year

# 対象者の絞り込み: 野手は 100打席以上 / 投手は 30イニング以上 または 登板10試合以上
MIN_PA = 100
MIN_IP = 30.0
MIN_G = 10

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
    role: str = ""  # "投手" / "野手"（多様性選抜用）
    league: str = ""  # "セ" / "パ"

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "metric": self.metric,
            "metric_label": self.metric_label,
            "player_slug": self.player_slug,
            "percentile": self.percentile,
            "role": self.role,
            "league": self.league,
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
    role: str = "",
    league: str = "",
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
        role=role,
        league=league,
    )


def build_pitcher_candidates(pitcher_df: pd.DataFrame) -> list[Candidate]:
    # セ・パは混ぜず、リーグごとにランキングを作る
    pitcher_df = add_league_column(pitcher_df)
    candidates: list[Candidate] = []
    spec = [
        ("stuff_plus", "Stuff+", "セイバー"),
        ("location_plus", "Location+", "セイバー"),
        ("k_minus_bb_pct", "K-BB%", "セイバー"),
        ("csw_pct", "CSW%", "セイバー"),
    ]
    for lg in LEAGUES:
        qualified = filter_qualified_pitcher(pitcher_df[pitcher_df["league"] == lg], MIN_IP, MIN_G)
        if qualified.empty:
            logger.warning("No qualified %s pitchers (IP>=%.0f or G>=%d)",
                           LEAGUE_FULL[lg], MIN_IP, MIN_G)
            continue

        for col, label, category in spec:
            c = _top_ranking_candidate(
                qualified, metric_col=col, metric_label=label,
                category=category, role="投手", league=lg,
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
                role="投手",
                league=lg,
            ))

    return candidates


def build_batter_candidates(batter_df: pd.DataFrame) -> list[Candidate]:
    # トラッキング系（Barrel%/HardHit% 等）は空のため、カウント系から ISO/K%/本塁打率 を補完
    batter_df = add_league_column(add_batter_derived_metrics(batter_df))
    candidates: list[Candidate] = []
    spec = [
        ("xwoba", "xwOBA", "セイバー", False),
        ("iso", "ISO", "セイバー", False),
        ("hr_pct", "本塁打率", "セイバー", False),
        ("k_pct", "K%", "セイバー", True),    # 低い順（三振が少ないほど優秀）
    ]
    for lg in LEAGUES:
        qualified = filter_qualified(batter_df[batter_df["league"] == lg], "pa", MIN_PA)
        if qualified.empty:
            logger.warning("No qualified %s batters (PA >= %d)", LEAGUE_FULL[lg], MIN_PA)
            continue

        for col, label, category, ascending in spec:
            c = _top_ranking_candidate(
                qualified, metric_col=col, metric_label=label, category=category,
                role="野手", league=lg, ascending=ascending,
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
                    role="野手",
                    league=lg,
                ))

    return candidates


def render_chart(cand: Candidate, tmpdir: Path, index: int) -> Path | None:
    """候補からグラフ画像を生成する。失敗時は None。"""
    out = tmpdir / f"chart_{index}.png"
    try:
        lg = f"{LEAGUE_FULL[cand.league]} " if cand.league in LEAGUE_FULL else ""
        title = f"{SEASON} {lg}{cand.metric_label}（{TODAY} 時点 / NPB Scholar）"
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


RANKING_DISPLAY_N = 5  # 本文の後に載せるランキングの表示人数


def _fmt_num(v: object) -> str:
    """数値を桁に応じて見やすく整形する（plus系は1桁、率系は小数3桁）。"""
    if isinstance(v, float):
        a = abs(v)
        if a >= 10:
            return f"{v:.1f}"
        if a >= 1:
            return f"{v:.2f}"
        return f"{v:.3f}"
    return str(v)


def format_ranking_block(df: pd.DataFrame, title: str, top_n: int = RANKING_DISPLAY_N) -> str:
    """summary_df を「1. 選手（チーム） 値」形式のランキング文字列に整形する。"""
    value_cols = [c for c in df.columns if c not in ("name", "team")]
    lines = [f"📊 {title}"]
    for i, (_, row) in enumerate(df.head(top_n).iterrows(), 1):
        name = str(row.get("name", "")).strip()
        team = str(row.get("team", "")).strip()
        who = f"{name}（{team}）" if team else name
        vals = " / ".join(_fmt_num(row[c]) for c in value_cols)
        lines.append(f"{i}. {who} {vals}")
    return "\n".join(lines)


def generate_and_insert(cand: Candidate, image_path: Path | None) -> None:
    """1件の候補について本文を生成し、ランキングを添えて Notion に登録する。"""
    data_summary = cand.summary_df.to_string(index=False)
    lg_full = LEAGUE_FULL.get(cand.league, "")
    metric_name = f"{lg_full}の{cand.metric_label}" if lg_full else cand.metric_label
    prompt = LEADERBOARD_TEMPLATE.format(
        metric_name=metric_name,
        metric_description=METRIC_DESCRIPTIONS.get(cand.metric, ""),
        data_summary=data_summary,
        recent_topics="（履歴なし）",
    )
    body = generate_draft(prompt)
    logger.info(f"Generated draft ({len(body)}字): {body[:50]}...")

    # 1位の選手の所属チームのハッシュタグを本文に追記
    if not cand.summary_df.empty:
        body = append_team_hashtag(body, cand.summary_df.iloc[0].get("team", ""))

    n = min(RANKING_DISPLAY_N, len(cand.summary_df))
    lg = f"{LEAGUE_FULL[cand.league]} " if cand.league in LEAGUE_FULL else ""
    ranking_block = format_ranking_block(cand.summary_df, f"{lg}{cand.metric_label} 上位{n}")
    full_body = f"{body}\n\n{ranking_block}\n\nデータ: NPB Scholar（{TODAY}時点）"

    insert_draft(
        body=full_body,
        category=cand.category,
        source_data=data_summary,
        image_path=str(image_path) if image_path else None,
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

    # score_candidates / select_diverse は dict を扱うので変換する。
    # metric が重複しうる（Stuff+ ランキングと散布図など）ため、添字 _uid で
    # 元の Candidate に確実にひも付ける。
    cand_dicts = []
    for i, c in enumerate(candidates):
        d = c.to_dict()
        d["_uid"] = i
        cand_dicts.append(d)

    ranked_dicts = score_candidates(cand_dicts)
    selected_dicts = select_diverse(ranked_dicts, MAX_DRAFTS)
    top = [candidates[d["_uid"]] for d in selected_dicts]
    chosen = " / ".join(f"{c.league}{c.role}:{c.metric_label}" for c in top)
    logger.info(f"Selected {len(top)}/{len(candidates)} candidates to post → {chosen}")

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
