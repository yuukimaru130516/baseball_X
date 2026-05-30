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
