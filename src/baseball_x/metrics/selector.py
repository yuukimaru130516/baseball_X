"""投稿コンテンツのスコアリングと候補選抜。"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from loguru import logger

from baseball_x.notion.client import query_open_drafts, query_recent_posts


def score_candidates(candidates: list[dict]) -> list[dict]:
    """コンテンツ候補にスコアを付与してソートする。

    スコア要素:
    - 指標値の極端さ（percentile が上位・下位10%）: +2
    - 過去7日以内に同カテゴリを投稿済み: -3
    - 過去14日以内に同メトリクスを投稿済み: -2
    - 過去30日以内に同選手を投稿済み: -1
    - すでに同メトリクスの下書きがある: -2 / 同カテゴリの下書きがある: -1
    - 日替わりの微小ジッタ: 0〜0.4（同点の並びを日ごとに変える）

    重複ペナルティは「投稿済」だけでなく「未投稿の下書き」も見るため、
    同じ日に生成を繰り返しても積み上がった下書きを避けて別テーマを選ぶ。
    """
    recent = _fetch_recent_posts(days=30)
    recent_categories_7d = {p["category"] for p in recent if _within_days(p["posted_at"], 7)}
    recent_metrics_14d = {p["metric"] for p in recent if _within_days(p["posted_at"], 14)}
    recent_players_30d = {p["player_slug"] for p in recent if p.get("player_slug")}

    drafts = _fetch_open_drafts()
    draft_metrics = {d["metric"] for d in drafts if d.get("metric")}
    draft_categories = {d["category"] for d in drafts if d.get("category")}

    rng = random.Random(date.today().toordinal())  # 日付シード（同日内は決定的）

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
        if c.get("metric") in draft_metrics:
            score -= 2
        if c.get("category") in draft_categories:
            score -= 1
        score += rng.uniform(0, 0.4)
        c["score"] = score

    return sorted(candidates, key=lambda x: x["score"], reverse=True)


def select_diverse(candidates: list[dict], k: int) -> list[dict]:
    """スコア順を尊重しつつ、役割・カテゴリ・指標が偏らないよう k 件選ぶ。

    score_candidates でソート済みの候補を貪欲に選び、すでに選んだものと
    役割（投手/野手）・カテゴリ・指標・リーグ（セ/パ）が重なるほど減点して
    次点を優先する。これにより「投手の Stuff+/Location+ だけが毎回採用される」
    状態や、毎回同じリーグだけが選ばれる偏りを防ぐ。
    """
    pool = sorted(candidates, key=lambda x: x.get("score", 0.0), reverse=True)
    selected: list[dict] = []
    while pool and len(selected) < k:
        best = None
        best_val = None
        for c in pool:
            penalty = 0.0
            penalty += sum(1 for s in selected if s.get("role") == c.get("role")) * 1.0
            penalty += sum(1 for s in selected if s.get("category") == c.get("category")) * 0.5
            penalty += sum(1 for s in selected if s.get("metric") == c.get("metric")) * 0.5
            penalty += sum(1 for s in selected if s.get("league") == c.get("league")) * 0.5
            val = c.get("score", 0.0) - penalty
            if best_val is None or val > best_val:
                best_val, best = val, c
        selected.append(best)
        pool.remove(best)
    return selected


def _fetch_recent_posts(days: int) -> list[dict]:
    """Notion から直近 N 日の投稿履歴を取得する。失敗時は空リスト。"""
    try:
        return query_recent_posts(days=days)
    except Exception as e:
        logger.warning(f"Failed to fetch recent posts from Notion: {e}")
        return []


def _fetch_open_drafts() -> list[dict]:
    """Notion から未投稿の下書きを取得する。失敗時は空リスト。"""
    try:
        return query_open_drafts()
    except Exception as e:
        logger.warning(f"Failed to fetch open drafts from Notion: {e}")
        return []


def _within_days(posted_at: str | None, days: int) -> bool:
    if not posted_at:
        return False
    try:
        dt = datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))
        return (date.today() - dt.date()) <= timedelta(days=days)
    except Exception:
        return False
