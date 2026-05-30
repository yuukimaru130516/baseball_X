"""投稿後のエンゲージメント取得。

posts_log を Supabase に保持していた旧構成から、Notion ページ自体を
SOR（Source of Record）として扱う構成に変更した。
"""
from __future__ import annotations

from loguru import logger

from baseball_x.notion.client import (
    extract_tweet_id,
    query_unmeasured_posts,
    update_post_metrics,
)
from baseball_x.publisher.x_client import get_client as get_x_client


def collect_metrics() -> None:
    """Status=投稿済 かつ未測定の Notion ページについて、X からメトリクスを取得して更新する。

    X Free tier ではインプレッション取得が困難なため、Likes / Retweets / Replies のみ取得を試みる。
    """
    pages = query_unmeasured_posts()
    if not pages:
        logger.info("No unmeasured posts found")
        return

    x = get_x_client()
    updated = 0
    for page in pages:
        page_id = page["page_id"]
        tweet_id = extract_tweet_id(page.get("post_url"))
        if not tweet_id:
            logger.warning(f"Skip page {page_id}: no tweet_id extractable from {page.get('post_url')}")
            continue
        try:
            tweet = x.get_tweet(tweet_id, tweet_fields=["public_metrics"])
            metrics = (tweet.data.public_metrics if tweet.data else {}) or {}
            update_post_metrics(
                page_id=page_id,
                likes=metrics.get("like_count"),
                retweets=metrics.get("retweet_count"),
                replies=metrics.get("reply_count"),
            )
            updated += 1
        except Exception as e:
            logger.warning(f"Failed to fetch metrics for tweet_id={tweet_id}: {e}")

    logger.info(f"Updated metrics for {updated}/{len(pages)} posts")
