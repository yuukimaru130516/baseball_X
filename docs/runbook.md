# Runbook

## セットアップ手順

### 1. 依存関係インストール

```bash
uv sync
```

### 2. 環境変数の設定

`.env.example` をコピーして各APIキーを設定。

```bash
cp .env.example .env
# .env を編集して各キーを入力
```

### 3. Notion セットアップ

1. Notionで新規ワークスペースを作成（UI操作 → https://www.notion.so/）
2. Integration を作成 → トークンを取得
3. 以下のスクリプトでDBを作成:
   ```bash
   uv run python - <<'EOF'
   from baseball_x.notion.client import create_post_database
   # 親ページIDはNotion URLから取得（32文字のID部分）
   db_id = create_post_database(parent_page_id="YOUR_PAGE_ID", title="投稿管理DB")
   print(f"NOTION_DATABASE_ID={db_id}")
   EOF
   ```
4. 出力された `NOTION_DATABASE_ID` を `.env` に設定

### 4. X 開発者アカウントセットアップ

1. https://developer.twitter.com でアプリを作成
2. OAuth 1.0a の Consumer Key / Secret を取得
3. Access Token / Secret を生成
4. `.env` に設定

### 5. GitHub Actionsのシークレット設定

リポジトリの Settings → Secrets and variables → Actions で以下を登録:

| シークレット名 | 内容 |
|--------------|------|
| NOTION_TOKEN | Notion Integration Token |
| NOTION_DATABASE_ID | 投稿管理DBのID |
| ANTHROPIC_API_KEY | Anthropic APIキー |
| X_API_KEY | X Consumer Key |
| X_API_SECRET | X Consumer Secret |
| X_ACCESS_TOKEN | X Access Token |
| X_ACCESS_SECRET | X Access Token Secret |

## 手動実行

```bash
# 下書き生成（npbscholar.com から JSON を取得 → Claude で本文生成 → Notion へ INSERT）
uv run python jobs/run_generator.py

# 投稿実行（Notion で Status=承認済 の下書きを X 投稿）
uv run python jobs/run_publisher.py

# 効果測定（投稿済ページの Likes / Retweets を Notion へ書き戻す）
uv run python jobs/run_analytics.py
```

## ジョブのタイムスケジュール（JST）

| ジョブ | 時刻 | GitHub Actions cron (UTC) |
|--------|------|--------------------------|
| generator | 朝7:00 | `0 22 * * *` |
| publisher (1回目) | 12:00 | `0 3 * * *` |
| publisher (2回目) | 19:00 | `0 10 * * *` |
| analytics | 翌朝5:30 | `30 20 * * *` |

## トラブルシュート

- **JSON 取得失敗**: npbscholar.com が一時的にダウンしている可能性。`curl -A Mozilla/5.0 https://npbscholar.com/data/batter_leaderboard_v1.json` で疎通確認
- **データ件数が少ない (`< 50 行` の警告)**: シーズン開始直後・オフ期や、提供側のスキーマ変更が発生している可能性
- **Notion DB に下書きが入らない**: Integration の権限がDBに付与されているか確認（DB右上「・・・」→「Add connections」）
- **X 投稿失敗**: Free tier の月間上限（500〜1500件）に達していないか確認
