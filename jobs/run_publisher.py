"""投稿実行ジョブ（GitHub Actions / Cloud Run Jobs から呼び出す）。"""
import sys

from loguru import logger

from baseball_x.notion.client import fetch_approved_drafts, mark_as_published
from baseball_x.publisher.x_client import post_tweet


def main() -> None:
    logger.info("Publisher job started")
    drafts = fetch_approved_drafts()

    if not drafts:
        logger.info("No approved drafts. Exiting.")
        return

    # 1件だけ処理（1日2回起動のため最大1件ずつ）
    draft = drafts[0]
    page_id = draft["id"]
    body = draft["properties"]["Body"]["rich_text"][0]["text"]["content"]

    logger.info(f"Posting: {body[:40]}...")
    tweet_id, tweet_url = post_tweet(text=body)
    mark_as_published(page_id=page_id, post_url=tweet_url)
    logger.info(f"Posted: {tweet_url}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Publisher job failed")
        sys.exit(1)
