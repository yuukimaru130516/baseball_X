"""Notionデータベースのスキーマ定義。"""

POST_DB_SCHEMA = {
    "Title": {"title": {}},
    "Body": {"rich_text": {}},
    "Status": {
        "select": {
            "options": [
                {"name": "下書き", "color": "gray"},
                {"name": "承認済", "color": "green"},
                {"name": "投稿済", "color": "blue"},
                {"name": "棄却", "color": "red"},
            ]
        }
    },
    "Category": {
        "select": {
            "options": [
                {"name": "セイバー", "color": "purple"},
                {"name": "トレンド", "color": "orange"},
                {"name": "比較", "color": "blue"},
                {"name": "選手", "color": "green"},
                {"name": "雑学", "color": "yellow"},
                {"name": "その他", "color": "default"},
            ]
        }
    },
    "Metric": {
        "select": {
            "options": [
                {"name": "Stuff+", "color": "purple"},
                {"name": "Location+", "color": "purple"},
                {"name": "K-BB%", "color": "purple"},
                {"name": "CSW%", "color": "purple"},
                {"name": "xwOBA", "color": "blue"},
                {"name": "ISO", "color": "blue"},
                {"name": "本塁打率", "color": "blue"},
                {"name": "K%", "color": "blue"},
                {"name": "Barrel%", "color": "blue"},
                {"name": "HardHit%", "color": "blue"},
                {"name": "Whiff%", "color": "blue"},
                {"name": "Chase%", "color": "blue"},
                {"name": "wOBA-xwOBA", "color": "orange"},
                {"name": "その他", "color": "default"},
            ]
        }
    },
    "PlayerSlug": {"rich_text": {}},
    "ScheduledAt": {"date": {}},
    "PostedAt": {"date": {}},
    "MeasuredAt": {"date": {}},
    "SourceData": {"rich_text": {}},
    "PostUrl": {"url": {}},
    "Impressions": {"number": {"format": "number"}},
    "Likes": {"number": {"format": "number"}},
    "Retweets": {"number": {"format": "number"}},
    "Replies": {"number": {"format": "number"}},
}


# 注目選手リスト用DB。活躍してインプレッションが伸びている選手を人間が登録し、
# run_player_spotlight が Status=未生成 の行を拾って下書きを作る。
SPOTLIGHT_DB_SCHEMA = {
    "PlayerName": {"title": {}},
    "Role": {
        "select": {
            "options": [
                {"name": "投手", "color": "blue"},
                {"name": "野手", "color": "orange"},
            ]
        }
    },
    "Status": {
        "select": {
            "options": [
                {"name": "未生成", "color": "yellow"},
                {"name": "生成済", "color": "green"},
                {"name": "停止", "color": "gray"},
            ]
        }
    },
    "Note": {"rich_text": {}},
    "RequestedAt": {"date": {}},
    "GeneratedAt": {"date": {}},
}
