"""投稿コンテンツのスコアリングと候補選抜。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from loguru import logger

from baseball_x.notion.client import query_recent_posts


def score_candidates(candidates: list[dict]) -> list[dict]:
    """コンテンツ候補にスコアを付与してソートする。

    スコア要素:
    - 指標値の極端さ（percentile が上位・下位10%）: +2
    - 過去7日以内に同カテゴリを投稿済み: -3
    - 過去14日以内に同メトリクスを投稿済み: -2
    - 過去30日以内に同選手を投稿済み: -1
    """
    recent = _fetch_recent_posts(days=30)
    recent_categories_7d = {p["category"] for p in recent if _within_days(p["posted_at"], 7)}
    recent_metrics_14d = {p["metric"] for p in recent if _within_days(p["posted_at"], 14)}
    recent_players_30d = {p["player_slug"] for p in recent if p.get("player_slug")}

    for c in candidates:
        score = 0.0
        pct = c.get("percentile", 50)
        if pct >= 90 or pct <= 10:
            score += 2
        if c.get("category") in recent_categories_7d:
            score -= 3
        if c.get("metric") in recent_metrics_14d:
            score -= 2
        if c.get("player_slug") in recent_players_30d:
            score -= 1
        c["score"] = score

    return sorted(candidates, key=lambda x: x["score"], reverse=True)


def _fetch_recent_posts(days: int) -> list[dict]:
    """Notion から直近 N 日の投稿履歴を取得する。失敗時は空リスト。"""
    try:
        return query_recent_posts(days=days)
    except Exception as e:
        logger.warning(f"Failed to fetch recent posts from Notion: {e}")
        return []


def _within_days(posted_at: str | None, days: int) -> bool:
    if not posted_at:
        return False
    try:
        dt = datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))
        return (date.today() - dt.date()) <= timedelta(days=days)
    except Exception:
        return False
