import json
import unittest

from services.content_asset_tool import (
    ContentAssetTool,
    MappingContentAssetAdapter,
    get_content_asset,
)


class ContentAssetToolTests(unittest.TestCase):
    def test_configured_adapter_returns_structured_content(self):
        result = ContentAssetTool.from_mapping(
            {
                "1001": {
                    "标题": "留学生求职准备",
                    "口播稿": "先看目标岗位，再安排准备节奏。",
                    "caption": "了解准备方法",
                    "hook": "为什么投了很多岗位仍没有反馈？",
                    "卖点": ["岗位拆解", "准备路径"],
                    "行动指引": "点击了解",
                    "标签": ["求职", "留学生"],
                    "来源": "content_review_fixture.json",
                }
            }
        ).get_content_asset(1001.0)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["found"])
        self.assertEqual(result["material_id"], "1001")
        self.assertEqual(result["title"], "留学生求职准备")
        self.assertEqual(result["script"], "先看目标岗位，再安排准备节奏。")
        self.assertEqual(result["selling_points"], ["岗位拆解", "准备路径"])
        self.assertEqual(result["tags"], ["求职", "留学生"])
        self.assertEqual(result["source"], "content_review_fixture.json")
        self.assertEqual(result["warnings"], [])
        json.dumps(result, ensure_ascii=False)

    def test_missing_adapter_is_explicitly_unavailable(self):
        result = get_content_asset("m1")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["asset_status"], "missing_adapter")
        self.assertIsNone(result["source"])
        self.assertIsNone(result["title"])
        self.assertIsNone(result["script"])
        self.assertTrue(any("不能用素材名称" in warning for warning in result["warnings"]))
        json.dumps(result, ensure_ascii=False)

    def test_adapter_without_requested_material_is_unavailable(self):
        result = ContentAssetTool(
            MappingContentAssetAdapter({"m1": {"title": ""}})
        ).get_content_asset("missing")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["asset_status"], "not_found")
        self.assertFalse(result["found"])
        self.assertIsNone(result["title"])
        json.dumps(result, ensure_ascii=False)

    def test_empty_record_is_not_treated_as_content(self):
        result = ContentAssetTool.from_mapping(
            {"m1": {"material_id": "m1", "material_name": "素材名称"}}
        ).get_content_asset("m1")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["asset_status"], "empty_record")
        self.assertIsNone(result["title"])
        self.assertIsNone(result["transcript"])
        json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
