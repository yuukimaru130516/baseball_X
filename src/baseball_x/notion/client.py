"""Notion APIクライアント。"""
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from loguru import logger
from notion_client import Client

from baseball_x.config import settings
from baseball_x.notion.schema import POST_DB_SCHEMA, SPOTLIGHT_DB_SCHEMA

_client: Client | None = None
_data_source_id: str | None = None
_spotlight_data_source_id: str | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = Client(auth=settings.notion_token)
    return _client


def get_data_source_id() -> str:
    """設定の database から data source ID を解決して返す（結果はキャッシュ）。

    Notion API は database 配下の data source 単位でページ・プロパティを扱うため、
    ページ作成・クエリにはこの data_source_id が必要。
    """
    global _data_source_id
    if _data_source_id is None:
        db = get_client().databases.retrieve(database_id=settings.notion_database_id)
        sources = db.get("data_sources") or []
        if not sources:
            raise RuntimeError(
                f"database {settings.notion_database_id} に data source が見つかりません"
            )
        _data_source_id = sources[0]["id"]
    return _data_source_id


def create_post_database(parent_page_id: str, title: str = "投稿管理DB") -> str:
    """承認フロー用のNotionデータベースを作成してそのIDを返す。

    Notion の新 API ではプロパティは database 直下ではなく initial_data_source 経由で
    設定する（旧来の properties= 引数は無視されるため使わない）。
    """
    client = get_client()
    response = client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": title}}],
        initial_data_source={"properties": POST_DB_SCHEMA},
    )
    return response["id"]


def upload_image_to_notion(image_path: str) -> str:
    """画像を Notion にアップロードして file_upload ID を返す（single_part）。"""
    client = get_client()
    p = Path(image_path)
    created = client.file_uploads.create(filename=p.name, content_type="image/png")
    upload_id = created["id"]
    with open(p, "rb") as f:
        client.file_uploads.send(file_upload_id=upload_id, file=(p.name, f, "image/png"))
    return upload_id


def insert_draft(
    body: str,
    category: str,
    source_data: str,
    image_path: str | None = None,
    metric: str | None = None,
    player_slug: str | None = None,
) -> str:
    """下書きをNotionDBにINSERTしてページIDを返す。

    image_path が与えられた場合は画像を Notion にアップロードし、ページ本文に
    画像ブロックとして添付する（publisher が投稿時に取得して X に添付できる）。
    """
    client = get_client()
    properties: dict = {
        "Title": {"title": [{"text": {"content": body[:50]}}]},
        "Body": {"rich_text": [{"text": {"content": body}}]},
        "Status": {"select": {"name": "下書き"}},
        "Category": {"select": {"name": category}},
        "SourceData": {"rich_text": [{"text": {"content": source_data[:2000]}}]},
    }
    if metric:
        properties["Metric"] = {"select": {"name": metric}}
    if player_slug:
        properties["PlayerSlug"] = {"rich_text": [{"text": {"content": player_slug}}]}

    children: list[dict] = []
    if image_path:
        try:
            upload_id = upload_image_to_notion(image_path)
            children.append({
                "object": "block",
                "type": "image",
                "image": {"type": "file_upload", "file_upload": {"id": upload_id}},
            })
        except Exception:
            logger.exception(f"Notion への画像アップロードに失敗（テキストのみで登録）: {image_path}")

    response = client.pages.create(
        parent={"type": "data_source_id", "data_source_id": get_data_source_id()},
        properties=properties,
        children=children or None,
    )
    return response["id"]


def fetch_page_image(page_id: str) -> str | None:
    """ページ本文の画像ブロックを取得し、一時ファイルへ保存してパスを返す。

    画像が無い・取得失敗時は None。呼び出し側で使用後に削除すること。
    """
    client = get_client()
    try:
        blocks = client.blocks.children.list(block_id=page_id)
    except Exception:
        logger.exception(f"ページのブロック取得に失敗: {page_id}")
        return None

    for block in blocks.get("results", []):
        if block.get("type") != "image":
            continue
        img = block["image"]
        url = (img.get("file") or {}).get("url") or (img.get("external") or {}).get("url")
        if not url:
            continue
        try:
            resp = httpx.get(url, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.write(resp.content)
            tmp.close()
            return tmp.name
        except Exception:
            logger.exception(f"画像ダウンロードに失敗: {url}")
            return None
    return None


def fetch_approved_drafts() -> list[dict]:
    """Status=承認済のレコードをすべて取得する。"""
    client = get_client()
    response = client.data_sources.query(
        data_source_id=get_data_source_id(),
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
    response = client.data_sources.query(
        data_source_id=get_data_source_id(),
        filter={
            "and": [
                {"property": "Status", "select": {"equals": "投稿済"}},
                {"property": "PostedAt", "date": {"on_or_after": cutoff}},
            ]
        },
    )
    return [_extract_post_meta(p) for p in response.get("results", [])]


def query_open_drafts() -> list[dict]:
    """まだ投稿していない下書き（Status=下書き）のメタ情報を返す。

    生成を繰り返したときに同じテーマの下書きが積み上がらないよう、
    スコアリングで「すでに下書きにあるテーマ」を避けるために使う。
    返り値の各要素は {page_id, category, metric, player_slug, posted_at} の dict。
    """
    client = get_client()
    response = client.data_sources.query(
        data_source_id=get_data_source_id(),
        filter={"property": "Status", "select": {"equals": "下書き"}},
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
    response = client.data_sources.query(
        data_source_id=get_data_source_id(),
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


def get_spotlight_data_source_id() -> str:
    """注目選手DBの data source ID を解決して返す（結果はキャッシュ）。"""
    global _spotlight_data_source_id
    if not settings.notion_spotlight_database_id:
        raise RuntimeError(
            "NOTION_SPOTLIGHT_DATABASE_ID が未設定です。"
            "create_spotlight_database で作成するか、--player 指定で実行してください。"
        )
    if _spotlight_data_source_id is None:
        db = get_client().databases.retrieve(
            database_id=settings.notion_spotlight_database_id
        )
        sources = db.get("data_sources") or []
        if not sources:
            raise RuntimeError(
                f"database {settings.notion_spotlight_database_id} に data source が見つかりません"
            )
        _spotlight_data_source_id = sources[0]["id"]
    return _spotlight_data_source_id


def create_spotlight_database(parent_page_id: str, title: str = "注目選手リスト") -> str:
    """注目選手リスト用のNotionデータベースを作成してそのIDを返す。"""
    response = get_client().databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": title}}],
        initial_data_source={"properties": SPOTLIGHT_DB_SCHEMA},
    )
    return response["id"]


def fetch_pending_spotlights() -> list[dict]:
    """Status=未生成 の注目選手を返す。

    各要素は {page_id, player_name, role, note} の dict。
    role は "投手"/"野手"/None。
    """
    response = get_client().data_sources.query(
        data_source_id=get_spotlight_data_source_id(),
        filter={"property": "Status", "select": {"equals": "未生成"}},
    )
    results = []
    for page in response.get("results", []):
        props = page.get("properties", {})
        titles = props.get("PlayerName", {}).get("title") or []
        notes = props.get("Note", {}).get("rich_text") or []
        role = props.get("Role", {}).get("select")
        results.append({
            "page_id": page.get("id"),
            "player_name": titles[0]["plain_text"] if titles else "",
            "role": role.get("name") if role else None,
            "note": notes[0]["plain_text"] if notes else "",
        })
    return [r for r in results if r["player_name"]]


def mark_spotlight_generated(page_id: str) -> None:
    """注目選手の Status=生成済 と GeneratedAt を更新する。"""
    get_client().pages.update(
        page_id=page_id,
        properties={
            "Status": {"select": {"name": "生成済"}},
            "GeneratedAt": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
        },
    )


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
