"""X (Twitter) API v2クライアント。"""
import tweepy

from baseball_x.config import settings

_client: tweepy.Client | None = None
_api_v1: tweepy.API | None = None


def get_client() -> tweepy.Client:
    global _client
    if _client is None:
        _client = tweepy.Client(
            consumer_key=settings.x_api_key,
            consumer_secret=settings.x_api_secret,
            access_token=settings.x_access_token,
            access_token_secret=settings.x_access_secret,
        )
    return _client


def get_api_v1() -> tweepy.API:
    """画像アップロード用のv1.1クライアント。"""
    global _api_v1
    if _api_v1 is None:
        auth = tweepy.OAuth1UserHandler(
            settings.x_api_key,
            settings.x_api_secret,
            settings.x_access_token,
            settings.x_access_secret,
        )
        _api_v1 = tweepy.API(auth)
    return _api_v1


def upload_image(image_path: str) -> str:
    """画像をアップロードしてmedia_idを返す（v1.1経由）。"""
    api = get_api_v1()
    media = api.media_upload(image_path)
    return str(media.media_id)


def post_tweet(text: str, image_path: str | None = None) -> tuple[str, str]:
    """ツイートを投稿してtweetIDとURLを返す。

    Returns:
        (tweet_id, tweet_url)
    """
    client = get_client()
    media_ids = None
    if image_path:
        media_id = upload_image(image_path)
        media_ids = [media_id]

    response = client.create_tweet(text=text, media_ids=media_ids)
    tweet_id = str(response.data["id"])
    # ユーザー名はアカウント設定に依存するため実行時に取得するのが理想だが暫定でIDベースURLを使用
    tweet_url = f"https://x.com/i/web/status/{tweet_id}"
    return tweet_id, tweet_url
