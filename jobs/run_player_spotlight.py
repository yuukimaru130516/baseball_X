"""特定選手スポットライト下書き生成ジョブ。

活躍してインプレッションが高い選手について、その選手のシーズン・
セイバーメトリクス・プロフィールを切り取った投稿の下書きを生成して
Notion に INSERT する。

選手の指定方法は2通り:
1. Notion「注目選手リスト」DB の Status=未生成 を順に処理（運用の主経路）
       uv run python jobs/run_player_spotlight.py
2. CLI で選手名を直接指定（手動・検証用。Notion DB 不要）
       uv run python jobs/run_player_spotlight.py --player "有原" --role pitcher
       uv run python jobs/run_player_spotlight.py --player "村上" --role batter --dry-run

データソースはシーズン累積のリーダーボードのため、内容は「今シーズンの
傾向」になる（単一試合の速報ではない）。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from loguru import logger

from baseball_x.data.npbscholar import fetch_batter_leaderboard, fetch_pitcher_leaderboard
from baseball_x.generator.llm_client import generate_draft
from baseball_x.generator.prompts import (
    METRIC_DESCRIPTIONS,
    PLAYER_SPOTLIGHT_TEMPLATE,
    append_team_hashtag,
)
from baseball_x.metrics.sabermetrics import (
    LEAGUE_FULL,
    add_batter_derived_metrics,
    add_league_column,
    filter_qualified,
    filter_qualified_pitcher,
    percentile_rank,
    rank_in_league,
)
from baseball_x.notion.client import fetch_pending_spotlights, insert_draft, mark_spotlight_generated
from baseball_x.visualization.charts import percentile_profile

TODAY = date.today()
SEASON = TODAY.year

# スポットライト対象の絞り込み条件
# 野手: 100打席以上 / 投手: 30イニング以上 または 登板10試合以上
MIN_PA = 100
MIN_IP = 30.0
MIN_G = 10

# 役割ごとの指標スペック: (カラム, 表示ラベル, Metric名, 整形種別, lower_is_better)
PITCHER_METRICS = [
    ("stuff_plus", "Stuff+", "Stuff+", "plus", False),
    ("location_plus", "Location+", "Location+", "plus", False),
    ("k_minus_bb_pct", "K-BB%", "K-BB%", "pct", False),
    ("csw_pct", "CSW%", "CSW%", "pct", False),
]
# Barrel%/HardHit%/Whiff%/Chase% はトラッキング系でデータが空のため、
# カウント系から導出できる指標（ISO/本塁打率/K%）と xwOBA を使う。
BATTER_METRICS = [
    ("xwoba", "xwOBA", "xwOBA", "rate3", False),
    ("iso", "ISO", "ISO", "rate3", False),
    ("hr_pct", "本塁打率", "本塁打率", "pct", False),
    ("k_pct", "K%", "K%", "pct", True),
]


@dataclass
class SpotlightRequest:
    player_name: str
    role: str | None  # "投手"/"野手"/None
    note: str = ""
    page_id: str | None = None  # Notion 由来なら更新用


def _fmt_value(value: float, kind: str) -> str:
    if kind == "rate3":
        return f"{value:.3f}"
    if kind == "pct":
        return f"{value:.1f}%"
    return f"{value:.0f}"  # plus / velo


def _normalize_name(s: str) -> str:
    return str(s).replace(" ", "").replace("　", "").strip()


def _find_player_row(df: pd.DataFrame, query: str) -> pd.Series | None:
    """player / name_romaji 列から選手名で1行を引き当てる。曖昧な場合は警告して先頭。"""
    if df.empty:
        return None
    q = _normalize_name(query)
    masks = []
    if "player" in df.columns:
        masks.append(df["player"].fillna("").map(_normalize_name).str.contains(q, case=False))
    if "name_romaji" in df.columns:
        masks.append(df["name_romaji"].fillna("").str.replace(" ", "").str.contains(q, case=False))
    if not masks:
        return None
    mask = masks[0]
    for m in masks[1:]:
        mask = mask | m
    hits = df[mask]
    if hits.empty:
        return None
    if len(hits) > 1:
        names = ", ".join(hits["player"].astype(str).tolist())
        logger.warning(f"'{query}' に複数ヒット: {names} → 先頭を採用")
    return hits.iloc[0]


def _build_profile(
    player_row: pd.Series, qualified: pd.DataFrame, metric_spec: list[tuple]
) -> tuple[list[dict], list[dict], str | None]:
    """選手1行から各指標のパーセンタイル・順位を計算する。

    Returns: (chart_rows, summary_rows, 最も優秀な Metric 名)
    """
    chart_rows: list[dict] = []
    summary_rows: list[dict] = []
    best_metric: str | None = None
    best_pct = -1
    for col, label, metric_name, kind, lower in metric_spec:
        if col not in qualified.columns:
            continue
        value = player_row.get(col)
        if value is None or pd.isna(value):
            continue
        pct = percentile_rank(qualified[col], value, lower_is_better=lower)
        rank = rank_in_league(qualified[col], value, lower_is_better=lower)
        if pct is None:
            continue
        value_text = _fmt_value(float(value), kind)
        chart_rows.append({"label": label, "percentile": pct, "value_text": value_text})
        summary_rows.append({
            "label": label, "value_text": value_text, "percentile": pct,
            "rank": rank, "qualified_n": int(qualified[col].notna().sum()),
            "metric_name": metric_name,
        })
        if pct > best_pct:
            best_pct, best_metric = pct, metric_name
    return chart_rows, summary_rows, best_metric


def _summary_text(rows: list[dict], league_full: str = "") -> str:
    lg = league_full or "リーグ"
    lines = []
    for r in rows:
        lines.append(
            f"- {r['label']}: {r['value_text']}"
            f"（{lg}規定{r['qualified_n']}人中{r['rank']}位 / 上位{100 - r['percentile']}%）"
        )
    return "\n".join(lines)


def process(req: SpotlightRequest, pitcher_df: pd.DataFrame, batter_df: pd.DataFrame,
            tmpdir: Path, index: int, dry_run: bool) -> bool:
    """1選手を処理して下書きを生成・登録する。成功で True。"""
    role = req.role
    # 規定到達者の中から探索する（シーズン傾向を語れる選手に限定し、
    # 規定外の投手が打者表に混じるノイズや不正な順位も防ぐ）。
    candidates: list[tuple[str, pd.DataFrame, list]] = []
    if role in (None, "投手"):
        candidates.append(("投手", filter_qualified_pitcher(pitcher_df, MIN_IP, MIN_G), PITCHER_METRICS))
    if role in (None, "野手"):
        candidates.append(("野手", filter_qualified(batter_df, "pa", MIN_PA), BATTER_METRICS))

    found_role = None
    player_row = None
    qualified = None
    metric_spec = None
    for r, qual_df, spec in candidates:
        row = _find_player_row(qual_df, req.player_name)
        if row is not None:
            found_role, player_row, qualified, metric_spec = r, row, qual_df, spec
            break

    if player_row is None:
        logger.warning(
            f"規定到達者に該当なし: '{req.player_name}'（role={role}）。"
            "規定投球回/打席に未到達か、名前・役割の指定を確認してください。"
        )
        return False

    team = str(player_row.get("team_short") or player_row.get("team") or "")
    player_name = str(player_row.get("player") or req.player_name)

    # セ・パは混ぜず、選手の所属リーグ内で順位・パーセンタイルを算出する
    league = player_row.get("league")
    league_full = LEAGUE_FULL.get(league, "")
    if league and "league" in qualified.columns:
        qualified = qualified[qualified["league"] == league]

    chart_rows, summary_rows, best_metric = _build_profile(player_row, qualified, metric_spec)
    if not summary_rows:
        logger.warning(f"'{player_name}' は規定到達指標が無く候補化できません")
        return False

    # チャート生成
    image_path: Path | None = None
    try:
        out = tmpdir / f"spotlight_{index}.png"
        lg = f"{league_full} " if league_full else ""
        title = f"{player_name}（{team}）{SEASON} {lg}プロフィール / NPB Scholar"
        image_path = percentile_profile(chart_rows, team=team, title=title, output_path=out)
    except Exception:
        logger.exception(f"チャート生成失敗: {player_name}")

    # 本文生成
    data_summary = _summary_text(summary_rows, league_full)
    note_line = f"参考メモ: {req.note}\n" if req.note else ""
    prompt = PLAYER_SPOTLIGHT_TEMPLATE.format(
        player_name=player_name,
        team=team,
        role=found_role,
        note_line=note_line,
        data_summary=data_summary,
    )
    # 主指標の説明を末尾に補足
    if best_metric and METRIC_DESCRIPTIONS.get(best_metric):
        prompt += f"\n\n指標補足: {METRIC_DESCRIPTIONS[best_metric]}"

    if dry_run:
        logger.info(f"[dry-run] {player_name}（{team}）\n{data_summary}")
        logger.info(f"[dry-run] chart={image_path}")
        return True

    body = generate_draft(prompt)
    logger.info(f"Generated spotlight draft ({len(body)}字): {body[:50]}...")
    body = append_team_hashtag(body, team)  # 当該選手の所属チームのハッシュタグ
    lg = f"{league_full} " if league_full else ""
    full_body = (
        f"{body}\n\n"
        f"📊 {player_name}（{team}）{SEASON}シーズン成績（{lg}内）\n{data_summary}\n\n"
        f"データ: NPB Scholar（{TODAY}時点）"
    )
    insert_draft(
        body=full_body,
        category="選手",
        source_data=data_summary,
        image_path=str(image_path) if image_path else None,
        metric=best_metric,
        player_slug=str(player_row.get("slug") or player_name),
    )
    if req.page_id:
        mark_spotlight_generated(req.page_id)
    return True


def _collect_requests(args: argparse.Namespace) -> list[SpotlightRequest]:
    if args.player:
        role = {"pitcher": "投手", "batter": "野手"}.get(args.role)
        return [SpotlightRequest(player_name=args.player, role=role, note=args.note or "")]
    # Notion 注目選手リストから取得
    pending = fetch_pending_spotlights()
    return [
        SpotlightRequest(
            player_name=p["player_name"], role=p.get("role"),
            note=p.get("note", ""), page_id=p["page_id"],
        )
        for p in pending
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="特定選手スポットライト下書き生成")
    parser.add_argument("--player", help="選手名（指定時は Notion リストを使わず単発生成）")
    parser.add_argument("--role", choices=["pitcher", "batter"], help="役割ヒント（--player と併用）")
    parser.add_argument("--note", help="投稿に添える参考メモ（--player と併用）")
    parser.add_argument("--dry-run", action="store_true", help="LLM 呼び出し・Notion 登録を行わず候補のみ表示")
    args = parser.parse_args()

    logger.info(f"Player spotlight job started: date={TODAY}")
    requests = _collect_requests(args)
    if not requests:
        logger.info("対象選手なし（Notion で Status=未生成 の行がありません）")
        return

    pitcher_df = add_league_column(fetch_pitcher_leaderboard(season=SEASON))
    batter_df = add_league_column(add_batter_derived_metrics(fetch_batter_leaderboard(season=SEASON)))

    ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmppath = Path(tmp)
        for i, req in enumerate(requests):
            try:
                if process(req, pitcher_df, batter_df, tmppath, i, args.dry_run):
                    ok += 1
            except Exception:
                logger.exception(f"処理失敗: {req.player_name}")
    logger.info(f"Player spotlight job completed: {ok}/{len(requests)} 件生成")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Player spotlight job failed")
        sys.exit(1)
