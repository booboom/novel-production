from novel_production_mcp.parsing import extract_json_object


def test_extract_fenced_json() -> None:
    result = extract_json_object('```json\n{"approved": true, "score": 90}\n```')
    assert result["approved"] is True


def test_extract_surrounded_json() -> None:
    result = extract_json_object('说明文字 {"chapter": 1, "items": []} 尾部')
    assert result["chapter"] == 1
