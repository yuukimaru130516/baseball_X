# NPB野球データ自動投稿X運用基盤 アーキテクチャ計画

## Context（背景・目的）

プロ野球（NPB）に興味があるXユーザーを集めることを目的に、目新しいデータを継続的に投稿してインプレッションを獲得する基盤を構築する。

差別化のキーは「目新しさ」：

- **最新のセイバーメトリクス指標**（Stuff+ / Location+ / xwOBA / Barrel% など Statcast 系）でマニア層に刺す
- 数値の意味を一言添えてライト層にも届く投稿にする

運用負荷を抑えるため、データ取得〜下書き生成は自動化し、最終的な投稿可否のみ人間がNotion上で承認するワークフローとする。月数百円以内の軽量クラウド構成を前提とする。

---

## ユーザー要件サマリ

| 項目 | 内容 |
|------|------|
| 対象リーグ | NPB（日本プロ野球） |
| データソース | **[NPB Scholar](https://npbscholar.com/) が公開する JSON リーダーボード** |
| コンテンツ方向性 | セイバーメトリクス指標（Stuff+ / xwOBA 等） |
| 自動化レベル | 下書き生成 → Notion承認 → 投稿 |
| 技術スタック | Python + GitHub Actions（月数百円以内） |
| 承認UI | Notionデータベース |
| ビジュアル | テキスト中心 + グラフ画像も自動生成 |
| 投稿頻度 | 1日1〜2投稿 |
| X APIティア | Free tierでスタート |
| LLM | Claude API（claude-haiku-4-5）で下書き生成 |
| 効果測定 | Notion DBに累積し分析 |

---

## アーキテクチャ全体像

```
┌─────────────────────────────────────────────────────────────┐
│                   GitHub Actions (cron)                      │
│  - 朝7:00：generatorジョブ起動                              │
│  - 12:00 / 19:00：publisherジョブ起動（承認済みのみ）        │
│  - 翌朝5:30：analyticsジョブ起動（前日投稿のメトリクス取得） │
└─────────────────────┬───────────────────────────────────────┘
                      │ workflow_dispatch / schedule
                      ▼
┌─────────────────────────────────────────────────────────────┐
│              Python ジョブ（ubuntu-latest）                  │
│  ┌──────────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ generator        │  │ publisher    │  │ analytics     │  │
│  │ (取得+下書き生成)│→ │ (X投稿)      │→ │ (Notion更新)  │  │
│  └────────┬─────────┘  └──────┬───────┘  └──────┬────────┘  │
└───────────┼───────────────────┼─────────────────┼───────────┘
            │                   │                 │
            ▼                   ▼                 ▼
   ┌──────────────────────────────────────────────────────┐
   │  Notion DB（投稿管理 = SOR）                          │
   │  - 下書き本文・グラフ画像・SourceData（検算用）       │
   │  - Status: 下書き → 承認済 → 投稿済 / 棄却           │
   │  - Likes / Retweets / Replies / PostUrl / PostedAt    │
   └──────────────────────────────────────────────────────┘
            ▲                                  ▲
            │ JSON取得                         │ 投稿
            │                                  │
   ┌────────────────────┐               ┌──────────────┐
   │ npbscholar.com     │               │  X (Twitter) │
   │ /data/*_leader...  │               └──────────────┘
   └────────────────────┘
```

データベース（Supabase 等）は持たない。**npbscholar.com の JSON を generator 起動時にオンデマンドで取得し、`pandas` で加工してそのまま下書き生成に使う**。投稿履歴・効果測定はすべて Notion DB 上で完結する。

---

## データソース詳細：npbscholar.com

### 調査結果（2026年5月）

| 確認項目 | 内容 |
|---------|------|
| robots.txt | `User-agent: * / Allow: /`（Cloudflare 経由、Content-Signal で `ai-train=no` 明示） |
| 利用規約 | About に「非公式・閲覧用」と明示。転載・スクレイピング禁止の明示なし |
| 著作権表記 | 「公式記録の置き換えではなく、公開情報をもとに再構成した分析補助サイト」 |
| 推奨運用 | 本格運用前にサイト運営者（Contact）へ用途を通知することが望ましい |

### エンドポイント

| 種別 | URL |
|-----|-----|
| 投手リーダーボード（現シーズン） | `https://npbscholar.com/data/pitcher_leaderboard_v1.json` |
| 打者リーダーボード（現シーズン） | `https://npbscholar.com/data/batter_leaderboard_v1.json` |
| 過去シーズン（投手） | `https://npbscholar.com/data/season/{year}/pitcher_leaderboard_v1.json` |
| 過去シーズン（打者） | `https://npbscholar.com/data/season/{year}/batter_leaderboard_v1.json` |

- 1 ファイル 約 400KB、`{generated_at, columns, rows}` 形式
- 規定打席に達した選手をほぼ全件含む（2026 投手 294 / 打者 372、2025 投手 361、2024 打者 478）

### 取得できる主な指標

**打者（28カラム）**:
- 基本: PA / AVG / OBP / SLG / OPS
- 期待値系: wOBA / xwOBA / xwOBA−wOBA / xBA / xSLG / xBA−BA / xSLG−SLG
- 打球品質系: HardHit% / Barrel% / SweetSpot% / GB%
- アプローチ系: Whiff% / Chase% / K% / BB%

**投手（42カラム）**:
- 基本: G / GS / W / L / SV / IP / H / R / ER / HR / BB / SO / ERA / WHIP
- 期待値系: xERA / FIP / xFIP / wOBA / xwOBA / BA / xBA / SLG / xSLG / OPS
- 球質系: Stuff+ / Location+ / FB Velo / FRV / BRV / ORV / PRV
- アプローチ系: K% / BB% / K-BB% / CSW% / Whiff% / Chase% / GB% / HardHit% / Barrel%

これらは baseballdata.jp や自前計算では実現困難な**サイト独自の差別化要素**である。

---

## レイヤー別の責務

### 1. データ取得レイヤー（`src/baseball_x/data/`）

**役割**: npbscholar.com の JSON エンドポイントを叩いて `pandas.DataFrame` を返す。

**実装**:
- `fetch_pitcher_leaderboard(season)` / `fetch_batter_leaderboard(season)`
- `httpx` + 指数バックオフ（最大3回リトライ）
- `MIN_EXPECTED_ROWS=50` を下回ったら `logger.warning`（オフ期 or スキーマ変更検知）
- スキーマバリデーション：`columns` と `rows` の存在確認のみ。詳細カラムの欠落は下流（generator）で吸収

### 2. 指標補助レイヤー（`src/baseball_x/metrics/`）

**役割**: 取得済み指標から複数を比較しやすくする補助関数のみ。自前計算（wOBA / FIP）は廃止。

- `filter_qualified(df, column, min_value)` — サンプル下限フィルタ
- `z_score(series)` — リーグ内 z-score
- `add_diff_column(df, a, b, out)` — `a - b` 派生列

### 3. コンテンツ生成レイヤー（`src/baseball_x/generator/` + `jobs/run_generator.py`）

**役割**: DataFrame から「投稿ネタ」を抽出し、Claude で下書きを生成して Notion に登録する。

**コンテンツ種別**:

| 種別 | ネタの例 | 使う指標 |
|------|---------|---------|
| 投手 球質 | 「Stuff+ TOP10」 | `stuff_plus` |
| 投手 コマンド | 「Location+ TOP10」 | `location_plus` |
| 投手 支配力 | 「K-BB% TOP10」「CSW% TOP10」 | `k_minus_bb_pct`, `csw_pct` |
| 投手 比較 | 「Stuff+ × Location+ 散布図」 | 2軸 |
| 打者 期待値 | 「xwOBA TOP10」 | `xwoba` |
| 打者 打球品質 | 「Barrel% / HardHit% TOP10」 | `barrel_pct`, `hard_hit_pct` |
| 打者 コンタクト | 「Whiff% LOW10」 | `whiff_pct` |
| 打者 選球眼 | 「Chase% LOW10」 | `chase_pct` |
| 打者 運の良し悪し | 「wOBA−xwOBA 乖離 TOP10」 | 差分 |

**コンテンツスコアリング**（`metrics/selector.py`）:
- 過去 7日以内に同カテゴリを投稿済みなら -3
- 過去 14日以内に同メトリクスを投稿済みなら -2
- 過去 30日以内に同選手を投稿済みなら -1
- 上位/下位 10% 以内の指標値なら +2

「最近の投稿履歴」は Notion DB に `query_recent_posts` を投げて取得する。

### 4. グラフ画像生成（`src/baseball_x/visualization/`）

- `matplotlib` + `japanize-matplotlib`
- 出力: 1200×675px（X カード推奨サイズ）
- `ranking_bar(... ascending=True/False)`: 上位／下位 N 棒グラフ
- `scatter_two_metrics`: 2軸散布図

### 5. LLM による下書き生成

- モデル: `claude-haiku-4-5`
- システムプロンプトは `prompts.SYSTEM_PROMPT` 固定 → **prompt caching でキャッシュヒット**
- ユーザーターンには「指標名・説明・上位選手データ・最近の類似投稿」を渡す
- 出力制約: 140字以内・ハッシュタグ 2〜3個・末尾に「データ: NPB Scholar」を明記
- 禁止: 断定表現・選手批判・煽り・差別的表現

### 6. 承認フロー（Notion）

**Notion DB スキーマ**（`notion/schema.py`）:

| プロパティ | 型 | 用途 |
|-----------|-----|------|
| Title | title | 下書き要約 |
| Body | rich_text | 投稿本文（編集可能） |
| Status | select | 下書き / 承認済 / 投稿済 / 棄却 |
| Category | select | セイバー / トレンド / 比較 / 雑学 / その他 |
| Metric | select | Stuff+ / Location+ / xwOBA など |
| PlayerSlug | rich_text | 重複ペナルティ用の識別子 |
| ScheduledAt | date | 投稿予定時刻 |
| PostedAt | date | publisher が記録 |
| MeasuredAt | date | analytics が記録 |
| SourceData | rich_text | 元データ（検算用） |
| PostUrl | url | 投稿後の X リンク |
| Impressions / Likes / Retweets / Replies | number | 効果測定 |

### 7. 投稿実行（`jobs/run_publisher.py`）

- `tweepy`（v2投稿 + v1.1 media/upload で画像添付）
- 1 回の起動で最大1件処理 → 1日2回起動 = 最大 2 投稿

### 8. 効果測定（`jobs/run_analytics.py` + `analytics/collector.py`）

- Notion の Status=投稿済 かつ MeasuredAt が未設定のページを取得
- `tweepy.get_tweet(tweet_id, tweet_fields=["public_metrics"])` で Likes / Retweets / Replies を取得
- Notion ページに書き戻し（`update_post_metrics`）

---

## ディレクトリ構造

```
baseball_x/
├── PLAN.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── src/baseball_x/
│   ├── config.py
│   ├── data/
│   │   └── npbscholar.py       # JSON 取得 → DataFrame
│   ├── metrics/
│   │   ├── sabermetrics.py     # 補助関数のみ（自前計算は廃止）
│   │   └── selector.py         # コンテンツスコアリング
│   ├── visualization/
│   │   └── charts.py           # matplotlib 描画
│   ├── generator/
│   │   ├── prompts.py          # システムプロンプト + 指標説明
│   │   └── llm_client.py       # prompt caching 対応
│   ├── notion/
│   │   ├── client.py           # 投稿管理 DB の SOR 操作
│   │   └── schema.py
│   ├── publisher/
│   │   └── x_client.py
│   └── analytics/
│       └── collector.py
├── jobs/
│   ├── run_generator.py
│   ├── run_publisher.py
│   └── run_analytics.py
├── tests/
├── .github/workflows/
│   ├── generator.yml
│   ├── publisher.yml
│   └── analytics.yml
└── docs/
    └── runbook.md
```

---

## データフロー（時系列）

```
[Day N]
07:00  generator
  → npbscholar.com から JSON を fetch（投手 + 打者の2リクエスト）
  → pandas で qualified（IP >= 20 / PA >= 100）にフィルタ
  → 指標ごとに候補を抽出（Stuff+ TOP10、xwOBA TOP10 等）
  → score_candidates でスコアリング、上位 2 件を選抜
  → matplotlib でグラフ画像生成
  → Claude API で投稿本文生成（prompt caching 有効）
  → Notion DB に Status=下書き で INSERT（Metric / PlayerSlug / SourceData 付き）

[任意のタイミング]
  ユーザー → Notion で本文確認・編集 → Status=承認済 に変更

12:00  publisher → 承認済 1件を X 投稿 → Notion を Status=投稿済 / PostUrl / PostedAt 更新
19:00  publisher → 承認済 1件を X 投稿 → 同上

[Day N+1]
05:30  analytics
  → Notion の Status=投稿済 かつ MeasuredAt 未設定のページを取得
  → tweepy で Likes / Retweets / Replies を取得
  → Notion に書き戻し
```

---

## 主要技術・ライブラリ一覧

| カテゴリ | 採用ライブラリ |
|---------|--------------|
| HTTP | `httpx` |
| データ処理 | `pandas` |
| 可視化 | `matplotlib`, `japanize-matplotlib` |
| LLM | `anthropic`（prompt caching 必須） |
| Notion | `notion-client` |
| X 投稿 | `tweepy` |
| パッケージ管理 | `uv` |
| 設定 | `pydantic-settings` |
| ログ | `loguru` |
| テスト | `pytest` |

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

## リスク・注意点

### 1. データソース依存

- npbscholar.com が停止 / スキーマ変更すると generator が落ちる
- → GitHub Actions のログでエラー検知。スキーマ変更時は手動修正
- → サイト運営者へ用途を通知して関係を作っておくのが望ましい

### 2. ai-train=no への対応

- npbscholar.com の robots.txt で Content-Signal `ai-train=no` が明示されている
- → **取得した数値を LLM の学習用途には使わない**（推論時の入力として使うのみ）
- → 投稿本文末尾に「データ: NPB Scholar」と出典を明記する

### 3. X API Free tier の制限

- 投稿数上限: 月 500〜1500 件（1日2投稿なら問題なし）
- 読み取り API も制限あり → analytics は失敗時にスキップする設計

### 4. 誤情報リスク

- Claude のハルシネーション対策として、本文内の数値は元データ（Notion `SourceData`）で目視確認
- 必要なら本文中の数値を自動検算するバリデーションを追加

### 5. 炎上リスク

- システムプロンプトで断定表現・批判・差別表現を禁止
- 承認フローで人間が最終チェック

---

## 段階的開発計画

### Phase 1: 疎通確認（1日）

- [ ] `uv sync` で依存をインストール
- [ ] `uv run python -c "from baseball_x.data import fetch_batter_leaderboard; print(fetch_batter_leaderboard().head())"` でデータ取得確認
- [ ] Notion Integration + DB 作成
- [ ] `uv run python jobs/run_generator.py` で Notion に下書き2件が入ることを確認

### Phase 2: 自動化（1週間）

- [ ] GitHub Actions の 3 ジョブ（generator / publisher / analytics）を有効化
- [ ] テスト投稿で 投稿 → 効果測定 までの一連を確認
- [ ] グラフ画像添付の確認（matplotlib 日本語フォント）

### Phase 3: 拡張（運用しながら）

- [ ] 候補スコアリングの精緻化（複数指標を組み合わせた z-score）
- [ ] 球種別データ（npbscholar の `pitch_type_leaders` JSON）の活用
- [ ] チーム別データ（`team_batting` / `team_pitching`）の活用
- [ ] 過去シーズン比較コンテンツ（過去シーズン JSON も取得して比較）

---

## 検証方法（end-to-end）

| ステップ | 確認コマンド・手順 |
|---------|-----------------|
| データ取得疎通 | `uv run python -c "from baseball_x.data import fetch_batter_leaderboard as f; print(f().head())"` で 5行以上が返る |
| ユニットテスト | `uv run pytest tests/test_npbscholar.py` |
| 下書き生成 | `uv run python jobs/run_generator.py` を実行し Notion に下書きが追加されること |
| X 投稿 | Notionでステータスを `承認済` に変更後、`uv run python jobs/run_publisher.py` を実行し X 投稿が成功すること |
| 効果測定 | 投稿の数時間後に `uv run python jobs/run_analytics.py` を実行し Notion の Likes / Retweets が更新されること |
| GitHub Actions | リポジトリを push し、Actions → generator を `workflow_dispatch` で手動実行してグリーンになること |
