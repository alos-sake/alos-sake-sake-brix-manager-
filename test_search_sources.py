import json
import sys
import types as pytypes
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from search_sources import (
    ProductCandidate,
    direct_source_search_url,
    MAX_AI_SEARCH_SOURCES,
    SEARCH_TIMEOUT_MS,
    DEFAULT_SEARCH_MODEL,
    SOURCE_REGISTRY,
    _canonical_path_without_query,
    _host_allowed,
    _source_matches_url,
    canonical_url,
    selected_source_map,
    grounded_product_search,
)


class SearchSourceTests(unittest.TestCase):
    def test_all_ten_requested_retailers_are_registered(self):
        retailers = [source for source in SOURCE_REGISTRY if source["kind"] == "retailer"]
        self.assertEqual(len(retailers), 10)
        names = {source["name"] for source in retailers}
        for name in ["勝鬨酒販", "はせがわ酒店", "いまでや酒店", "山中酒の店（大阪）", "住吉酒販", "登酒店", "佐野屋 JIZAKE.COM", "酒専門店鍵や", "矢島酒店", "マツザキ"]:
            self.assertIn(name, names)

    def test_yamanaka_is_osaka_yamanaka_sake_shop_not_yamanakaya(self):
        source = next(item for item in SOURCE_REGISTRY if item["id"] == "yamanaka")
        self.assertEqual(source["domain"], "yamanaka-sake.shop")
        self.assertIn("山中酒の店", source["name"])

    def test_exact_and_subdomains_match_selected_retailer(self):
        source = next(item for item in SOURCE_REGISTRY if item["id"] == "katidoki")
        self.assertTrue(_source_matches_url("https://katidoki.shop/items/123", source))
        self.assertTrue(_source_matches_url("https://www.katidoki.shop/items/123", source))
        self.assertFalse(_source_matches_url("https://katidoki.shop.example.net/items/123", source))

    def test_github_dataset_requires_exact_repository_path(self):
        source = next(item for item in SOURCE_REGISTRY if item["id"] == "sakeopendata")
        self.assertTrue(_source_matches_url("https://github.com/Code-for-SAKE/SAKEOpenData", source))
        self.assertTrue(_source_matches_url("https://github.com/Code-for-SAKE/SAKEOpenData/tree/main", source))
        self.assertFalse(_source_matches_url("https://github.com/Code-for-SAKE/other", source))
        self.assertFalse(_source_matches_url("https://github.com/Code-for-SAKE/SAKEOpenData-impostor", source))

    def test_canonical_url_rejects_non_http_and_removes_www(self):
        self.assertEqual(canonical_url("https://www.example.com/a/?utm_source=test"), "https://example.com/a?utm_source=test")
        self.assertIsNone(canonical_url("javascript:alert(1)"))
        self.assertEqual(_canonical_path_without_query("https://example.com/product?ref=abc"), "https://example.com/product")

    def test_direct_search_link_is_fast_encoded_and_source_scoped(self):
        url = direct_source_search_url("ほうおうびでん", "hasegawa")
        self.assertTrue(url.startswith("https://www.google.com/search?"))
        self.assertIn("site%3Ahasegawasaketen.com", url)
        self.assertIn("%E3%81%BB", url)
        self.assertNotIn("javascript:", url)
        generic_url = direct_source_search_url("鳳凰美田", "general")
        self.assertIn("%E6%97%A5%E6%9C%AC%E9%85%92", generic_url)
        self.assertNotIn("%E3%81%9D%E3%81%AE%E4%BB%96%E3%81%AE%E3%82%A6%E3%82%A7%E3%83%96", generic_url)

    def test_search_has_bounded_defaults_for_speed_and_free_quota(self):
        self.assertEqual(MAX_AI_SEARCH_SOURCES, 3)
        self.assertEqual(SEARCH_TIMEOUT_MS, 15000)
        self.assertEqual(DEFAULT_SEARCH_MODEL, "gemini-2.5-flash")

    def test_grounded_search_uses_separate_model_short_timeout_and_no_retries(self):
        captured = {}

        class FakeInteractions:
            def create(self, **kwargs):
                captured["request"] = kwargs
                return SimpleNamespace(output_text=json.dumps({"candidates": [], "search_notes": []}), steps=[])

        class FakeClient:
            def __init__(self, *, api_key, http_options):
                captured["api_key"] = api_key
                captured["http_options"] = http_options
                self.interactions = FakeInteractions()

        fake_genai = pytypes.ModuleType("google.genai")
        fake_genai.Client = FakeClient
        fake_types = pytypes.ModuleType("google.genai.types")
        fake_types.HttpOptions = lambda **kwargs: SimpleNamespace(**kwargs)
        fake_types.HttpRetryOptions = lambda **kwargs: SimpleNamespace(**kwargs)
        fake_google = pytypes.ModuleType("google")
        fake_google.genai = fake_genai
        fake_genai.types = fake_types

        with patch.dict(sys.modules, {
            "google": fake_google,
            "google.genai": fake_genai,
            "google.genai.types": fake_types,
        }):
            result = grounded_product_search("test-key", "gemini-2.5-flash", "鳳凰美田", ["hasegawa"])

        self.assertEqual(captured["request"]["model"], "gemini-2.5-flash")
        self.assertEqual(captured["http_options"].timeout, 15000)
        self.assertEqual(captured["http_options"].retry_options.attempts, 1)
        self.assertEqual(captured["request"]["tools"], [{"type": "google_search"}])
        self.assertEqual(result["candidates"], [])

    def test_selected_sources_are_only_requested_sources(self):
        items = selected_source_map(["katidoki", "matsuzaki"])
        self.assertEqual([item["id"] for item in items], ["katidoki", "matsuzaki"])
        self.assertFalse(_host_allowed("https://hasegawasaketen.com/eshop", items))

    def test_candidate_schema_supports_kana_and_numeric_specs(self):
        candidate = ProductCandidate(product_name="テスト銘柄", source_id="katidoki", name_kana="てすとめいがら", alcohol=15.5)
        self.assertEqual(candidate.name_kana, "てすとめいがら")
        self.assertEqual(candidate.alcohol, 15.5)
        schema = ProductCandidate.model_json_schema()
        self.assertIn("name_kana", schema["properties"])


if __name__ == "__main__":
    unittest.main()
