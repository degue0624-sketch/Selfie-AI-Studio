REVIEW_FIELDS = (
    ("face", "顔"),
    ("eyes", "目"),
    ("hair", "髪"),
    ("background", "背景"),
    ("lighting", "光源"),
    ("composition", "構図"),
    ("pose", "ポーズ"),
    ("hands", "手・指"),
    ("overall", "全体"),
)

REVIEW_VALUES = ("未評価", "良い", "普通", "要修正")


def normalize_review(data):
    result = {
        "ratings": {key: "未評価" for key, _ in REVIEW_FIELDS},
        "notes": "",
        "updated_at": "",
    }
    if not isinstance(data, dict):
        return result

    ratings = data.get("ratings") or {}
    if isinstance(ratings, dict):
        for key, _ in REVIEW_FIELDS:
            value = ratings.get(key)
            if value in REVIEW_VALUES:
                result["ratings"][key] = value

    result["notes"] = str(data.get("notes") or "")
    result["updated_at"] = str(data.get("updated_at") or "")
    return result
