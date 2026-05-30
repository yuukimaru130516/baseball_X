"""Notion APIクライアント。"""
from datetime import date, datetime, timedelta, timezone

from notion_client import Client

from baseball_x.config import settings
from baseball_x.notion.schema import POST_DB_SCHEMA

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = Client(auth=settings.notion_token)
    return _client


def create_post_database(parent_page_id: str, title: str = "投稿管理DB") -> str:
    """承認フロー用のNotionデータベースを作成してそのIDを返す。"""
    client = get_client()
    response = client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": title}}],
        properties=POST_DB_SCHEMA,
    )
    return response["id"]


def insert_draft(
    body: str,
    category: str,
    source_data: str,
    image_url: str | None = None,
    metric: str | None = None,
    player_slug: str | None = None,
) -> str:
    """下書きをNotionDBにINSERTしてページIDを返す。"""
    client = get_client()
    properties: dict = {
        "Title": {"title": [{"text": {"content": body[:50]}}]},
        "Body": {"rich_text": [{"text": {"content": body}}]},
        "Status": {"select": {"name": "下書き"}},
        "Category": {"select": {"name": category}},
        "SourceData": {"rich_text": [{"text": {"content": source_data}}]},
    }
    if metric:
        properties["Metric"] = {"select": {"name": metric}}
    if player_slug:
        properties["PlayerSlug"] = {"rich_text": [{"text": {"content": player_slug}}]}
    response = client.pages.create(
        parent={"database_id": settings.notion_database_id},
        properties=properties,
    )
    return response["id"]


def fetch_approved_drafts() -> list[dict]:
    """Status=承認済のレコードをすべて取得する。"""
    client = get_client()
    response = client.databases.query(
        database_id=settings.notion_database_id,
        filter={"property": "Status", "select": {"equals": "承認済"}},
    )
    return response.get("results", [])


def mark_as_published(page_id: str, post_url: str) -> None:
    """投稿完了後にStatus=投稿済とPostUrlを更新する。"""
    client = get_client()
    client.pages.update(
        page_id=page_id,
        properties={
            "Status": {"select": {"name": "投稿済"}},
            "PostUrl": {"url": post_url},
            "PostedAt": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
        },
    )


def query_recent_posts(days: int = 30) -> list[dict]:
    """直近N日に投稿（Status=投稿済）したページのメタ情報を返す。

    スコアリングの重複ペナルティ用。返り値の各要素は
    {page_id, category, metric, player_slug, posted_at} の dict。
    """
    client = get_client()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    response = client.databases.query(
        database_id=settings.notion_database_id,
        filter={
            "and": [
                {"property": "Status", "select": {"equals": "投稿済"}},
                {"property": "PostedAt", "date": {"on_or_after": cutoff}},
            ]
        },
    )
    return [_extract_post_meta(p) for p in response.get("results", [])]


def update_post_metrics(
    page_id: str,
    likes: int | None,
    retweets: int | None,
    replies: int | None = None,
    impressions: int | None = None,
) -> None:
    """投稿のエンゲージメント指標を更新する。"""
    client = get_client()
    properties: dict = {
        "MeasuredAt": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
    }
    if likes is not None:
        properties["Likes"] = {"number": likes}
    if retweets is not None:
        properties["Retweets"] = {"number": retweets}
    if replies is not None:
        properties["Replies"] = {"number": replies}
    if impressions is not None:
        properties["Impressions"] = {"number": impressions}
    client.pages.update(page_id=page_id, properties=properties)


def query_unmeasured_posts() -> list[dict]:
    """Status=投稿済 かつ MeasuredAt が未設定のページを返す。"""
    client = get_client()
    response = client.databases.query(
        database_id=settings.notion_database_id,
        filter={
            "and": [
                {"property": "Status", "select": {"equals": "投稿済"}},
                {"property": "MeasuredAt", "date": {"is_empty": True}},
            ]
        },
    )
    return [_extract_post_meta(p) for p in response.get("results", [])]


def _extract_post_meta(page: dict) -> dict:
    """Notion page オブジェクトから {page_id, category, metric, player_slug, posted_at, post_url} を抽出する。"""
    props = page.get("properties", {})

    def _select(name: str) -> str | None:
        v = props.get(name, {}).get("select")
        return v.get("name") if v else None

    def _rich_text(name: str) -> str | None:
        rts = props.get(name, {}).get("rich_text") or []
        return rts[0]["plain_text"] if rts else None

    def _date(name: str) -> str | None:
        d = props.get(name, {}).get("date")
        return d.get("start") if d else None

    def _url(name: str) -> str | None:
        return props.get(name, {}).get("url")

    return {
        "page_id": page.get("id"),
        "category": _select("Category"),
        "metric": _select("Metric"),
        "player_slug": _rich_text("PlayerSlug"),
        "posted_at": _date("PostedAt"),
        "post_url": _url("PostUrl"),
    }


def extract_tweet_id(post_url: str | None) -> str | None:
    """投稿 URL から tweet_id を抽出する（例: https://x.com/user/status/123 → 123）。"""
    if not post_url:
        return None
    parts = post_url.rstrip("/").split("/")
    if "status" in parts:
        i = parts.index("status")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None
