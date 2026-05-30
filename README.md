# baseball_x

NPB（日本プロ野球）の統計データを **npbscholar.com** から取得し、Stuff+ / xwOBA / Barrel% などの最新セイバーメトリクス指標を題材にした投稿の下書きを自動生成して、人間が Notion で承認したものだけを X に投稿する運用基盤。

---

## 概要

| 項目 | 内容 |
|------|------|
| 対象リーグ | NPB（日本プロ野球） |
| データソース | [NPB Scholar](https://npbscholar.com/) が公開する JSON リーダーボード |
| コンテンツ方向性 | セイバーメトリクス（Stuff+ / Location+ / xwOBA / Barrel% など） |
| 自動化レベル | 下書き自動生成 → Notion で人間が承認 → X 投稿 |
| 投稿頻度 | 1日1〜2投稿 |
| 月額コスト目安 | 200〜500円（Claude Haiku のみ実費） |

---

## アーキテクチャ

```
[GitHub Actions cron]
  │
  ├── 朝7時 → generator
  │     └─ npbscholar.com から JSON を fetch
  │        → 候補抽出 → matplotlib でグラフ生成
  │        → Claude API で下書き生成
  │        → Notion DB に Status=下書き で INSERT
  │
  ├── 12時 / 19時 → publisher
  │     └─ Notion DB の Status=承認済 を1件取得 → X 投稿
  │        → Notion を Status=投稿済 + PostUrl で更新
  │
  └── 翌朝5時半 → analytics
        └─ Notion DB の Status=投稿済 で未測定のページを取得
           → X API から Likes / Retweets を取得 → Notion に書き戻す
```

データベースは持たない。**npbscholar.com の JSON（投手 約 300 / 打者 約 380 行 × 数十カラム）をオンデマンドで取得**し、`pandas` で加工してそのまま使う。投稿履歴・効果測定は Notion DB に集約。

---

## セットアップ

### 必要なアカウント

- [Notion](https://www.notion.so/) — 個人プラン（無料）
- [X Developer Portal](https://developer.twitter.com/) — Free tier
- [Anthropic API](https://console.anthropic.com/) — `claude-haiku-4-5` を使用

### 1. クローン & 依存関係インストール

```bash
git clone <your-repo-url>
cd baseball_x
uv sync
```

### 2. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して各APIキーを入力
```

| 変数名 | 取得元 |
|--------|--------|
| `NOTION_TOKEN` | Notion Integration（内部インテグレーション） |
| `NOTION_DATABASE_ID` | 後述のスクリプトで作成 |
| `ANTHROPIC_API_KEY` | Anthropic Console |
| `X_API_KEY` / `X_API_SECRET` / `X_ACCESS_TOKEN` / `X_ACCESS_SECRET` | X Developer Portal |

### 3. Notion データベース作成

1. Notion で任意のページを作成（Integration を「Add connections」で連携）
2. ページ ID を確認したうえで以下を実行:

```bash
uv run python - <<'EOF'
from baseball_x.notion.client import create_post_database
db_id = create_post_database(parent_page_id="YOUR_PAGE_ID", title="投稿管理DB")
print(f"NOTION_DATABASE_ID={db_id}")
EOF
```

3. 出力された `NOTION_DATABASE_ID` を `.env` に設定

### 4. GitHub Actions シークレット登録

リポジトリの **Settings → Secrets and variables → Actions** で `.env` の全キーを登録。

---

## 使い方

### 手動実行

```bash
# 下書き生成（npbscholar から取得 → Claude 生成 → Notion 登録）
uv run python jobs/run_generator.py

# 投稿実行（Notion で承認済みのものを X 投稿）
uv run python jobs/run_publisher.py

# 効果測定（いいね数・RT数を Notion に書き戻す）
uv run python jobs/run_analytics.py
```

### データ取得の単体確認

```bash
uv run python -c "from baseball_x.data import fetch_batter_leaderboard; print(fetch_batter_leaderboard().head())"
```

### 自動実行（GitHub Actions）

| ジョブ | 実行時刻 |
|--------|---------|
| generator | 毎日 7:00 |
| publisher | 毎日 12:00 / 19:00 |
| analytics | 毎日 5:30 |

各ジョブは **Actions** タブから手動トリガーも可能。

---

## ディレクトリ構造

```
baseball_x/
├── src/baseball_x/
│   ├── config.py              # 環境変数管理
│   ├── data/                  # npbscholar.com 取得モジュール
│   ├── metrics/               # フィルタ・z-score 等の補助
│   ├── visualization/         # グラフ画像生成
│   ├── generator/             # Claude API 下書き生成
│   ├── notion/                # Notion API 連携（投稿 DB の SOR）
│   ├── publisher/             # X API 投稿
│   └── analytics/             # 効果測定
├── jobs/                      # 各ジョブのエントリポイント
├── .github/workflows/         # GitHub Actions 定義
├── docs/runbook.md            # 詳細セットアップ手順
└── PLAN.md                    # アーキテクチャ設計ドキュメント
```

---

## 技術スタック

| カテゴリ | 採用技術 |
|---------|---------|
| 言語 | Python 3.11+ |
| パッケージ管理 | uv |
| HTTP | httpx |
| データ処理 | pandas |
| グラフ生成 | matplotlib, japanize-matplotlib |
| LLM | Anthropic Claude API (`claude-haiku-4-5`, prompt caching) |
| 承認フロー | Notion API |
| 投稿 | tweepy (X API v2) |
| スケジューラ | GitHub Actions cron |

---

## コスト試算（月額）

| 項目 | 想定額 |
|------|--------|
| GitHub Actions | 0円（無料枠内） |
| Notion | 0円（個人プラン） |
| Claude API (Haiku + prompt caching) | 200〜500円 |
| X API Free tier | 0円 |
| **合計** | **約200〜500円** |

---

## データソースに関する注意

- 取得元: [NPB Scholar](https://npbscholar.com/) — 「公式記録の置き換えではなく、公開情報をもとに再構成した閲覧・分析補助サイト」と明示
- robots.txt: `User-agent: * / Allow: /`、転載・スクレイピング禁止の明示なし
- Content-Signal で `ai-train=no` が指定されているため、**取得した数値を LLM の学習に使うことは禁止**。本リポジトリでは生成 LLM への入力（推論用途）に留めている
- 投稿本文の末尾には必ず「データ: NPB Scholar」と出典を明記する
- 取得頻度は generator ジョブの 1日1回 + 手動実行のみ。負荷をかけない運用とする
- 長期運用前にサイト運営者（Contact）へ用途を通知することが望ましい

### LLM が生成した数値の検算

Claude には JSON から抽出した数値だけを渡しているが、生成本文にハルシネーションが混入していないか、Notion の `SourceData` フィールドで元データと突き合わせて確認すること。
