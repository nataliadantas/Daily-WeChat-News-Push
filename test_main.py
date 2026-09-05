import datetime
import sys
import types
import unittest
from unittest.mock import Mock, patch


# 本地验证不依赖第三方 requests 包；GitHub Actions 仍会安装真实依赖。
if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")
    requests_stub.RequestException = Exception
    requests_stub.get = None
    requests_stub.post = None
    sys.modules["requests"] = requests_stub

import main


class FakeResponse:
    def __init__(self, text, status_code=200, encoding="utf-8"):
        self.text = text
        self.content = text.encode(encoding)
        self.status_code = status_code


class MarketDataTests(unittest.TestCase):
    def test_market_fields_are_parsed_from_provider_payloads(self):
        quote_time = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        ).strftime("%Y%m%d%H%M%S")
        quote_date = datetime.datetime.strptime(
            quote_time[:8], "%Y%m%d"
        ).strftime("%Y-%m-%d")

        simplified_stock = (
            'v_s_sh000001="1~上证指数~000001~3985.70~-0.60~-0.02~0";\n'
            'v_s_sz399001="51~深证成指~399001~13915.86~-99.14~-0.71~0";\n'
            'v_s_sz399006="51~创业板指~399006~3408.14~-30.54~-0.89~0";'
        )
        full_stock = (
            f'v_sh000001="1~上证指数~000001~3985.70~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~{quote_time}~-0.60~-0.02";\n'
            f'v_sz399001="51~深证成指~399001~13915.86~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~{quote_time}~-99.14~-0.71";\n'
            f'v_sz399006="51~创业板指~399006~3408.14~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~{quote_time}~-30.54~-0.89";'
        )
        gold = (
            f'var hq_str_hf_GC="4480.81,0,0,0,0,0,{quote_time[8:10]}:{quote_time[10:12]}:{quote_time[12:14]},0,0,0,0,0,{quote_date},纽约黄金";\n'
            f'var hq_str_gds_AUTD="958.37,0,0,0,0,0,{quote_time[8:10]}:{quote_time[10:12]}:{quote_time[12:14]},0,0,0,0,0,{quote_date},黄金延期";'
        )

        def fake_get(url, **_kwargs):
            if "qt.gtimg.cn" in url:
                return FakeResponse(
                    simplified_stock if "q=s_" in url else full_stock
                )
            if "hq.sinajs.cn" in url:
                return FakeResponse(gold)
            raise AssertionError(f"unexpected URL: {url}")

        with patch("main.requests.get", side_effect=fake_get):
            market = main.fetch_realtime_market_data()

        self.assertEqual(market["sh_index"], "上证指数：3985.70 点（-0.02%）")
        self.assertEqual(market["sz_index"], "深证成指：13915.86 点（-0.71%）")
        self.assertEqual(market["cy_index"], "创业板指：3408.14 点（-0.89%）")
        self.assertEqual(market["gold_intl"], "4480.81 美元/盎司")
        self.assertEqual(market["gold_cn"], "958.37 元/克")

    def test_incomplete_stock_payload_is_not_presented_as_partial_success(self):
        quote_time = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        ).strftime("%Y%m%d%H%M%S")
        quote_date = datetime.datetime.strptime(
            quote_time[:8], "%Y%m%d"
        ).strftime("%Y-%m-%d")
        one_stock_only = (
            f'v_sh000001="1~上证指数~000001~3985.70~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~{quote_time}~-0.60~-0.02";'
        )
        gold = (
            f'var hq_str_hf_GC="4480.81,0,0,0,0,0,{quote_time[8:10]}:{quote_time[10:12]}:{quote_time[12:14]},0,0,0,0,0,{quote_date},纽约黄金";\n'
            f'var hq_str_gds_AUTD="958.37,0,0,0,0,0,{quote_time[8:10]}:{quote_time[10:12]}:{quote_time[12:14]},0,0,0,0,0,{quote_date},黄金延期";'
        )

        def fake_get(url, **_kwargs):
            return FakeResponse(one_stock_only if "qt.gtimg.cn" in url else gold)

        with patch("main.requests.get", side_effect=fake_get):
            market = main.fetch_realtime_market_data()

        self.assertEqual(market["sh_index"], "上证指数：数据暂不可用")
        self.assertEqual(market["sz_index"], "深证成指：数据暂不可用")
        self.assertEqual(market["cy_index"], "创业板指：数据暂不可用")
        self.assertEqual(market["stock_updated_at"], "未获取")

    def test_stale_market_quotes_are_rejected(self):
        stale_time = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        ) - datetime.timedelta(days=10)
        quote_time = stale_time.strftime("%Y%m%d%H%M%S")
        quote_date = stale_time.strftime("%Y-%m-%d")
        stock = (
            f'v_sh000001="1~上证指数~000001~3985.70~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~{quote_time}~-0.60~-0.02";\n'
            f'v_sz399001="51~深证成指~399001~13915.86~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~{quote_time}~-99.14~-0.71";\n'
            f'v_sz399006="51~创业板指~399006~3408.14~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~{quote_time}~-30.54~-0.89";'
        )
        gold = (
            f'var hq_str_hf_GC="4480.81,0,0,0,0,0,{quote_time[8:10]}:{quote_time[10:12]}:{quote_time[12:14]},0,0,0,0,0,{quote_date},纽约黄金";\n'
            f'var hq_str_gds_AUTD="958.37,0,0,0,0,0,{quote_time[8:10]}:{quote_time[10:12]}:{quote_time[12:14]},0,0,0,0,0,{quote_date},黄金延期";'
        )

        def fake_get(url, **_kwargs):
            return FakeResponse(stock if "qt.gtimg.cn" in url else gold)

        with patch("main.requests.get", side_effect=fake_get):
            market = main.fetch_realtime_market_data()

        self.assertEqual(market["sh_index"], "上证指数：数据暂不可用")
        self.assertEqual(market["gold_intl"], "数据暂不可用")
        self.assertEqual(market["stock_updated_at"], "未获取")
        self.assertEqual(market["gold_updated_at"], "未获取")
        self.assertEqual(len(market["warnings"]), 2)


class RewritePipelineTests(unittest.TestCase):
    def test_section_uses_model_source_and_program_date(self):
        model_result = (
            '{"items":[{"title_cn":"第一段 第二段",'
            '"summary_cn":"这是模型联网阅读原文后撰写的测试正文，包含新闻六要素。",'
            '"original_url":"https://reuters.com/world/original",'
            '"source":"Reuters"}]}'
        )

        def fake_gpt(prompt):
            self.assertIn("Ukraine Russia war", prompt)
            return model_result

        with patch("main.call_gpt_web_rewrite", side_effect=fake_gpt):
            section_html = main.process_section("俄乌冲突与前线战况", "Ukraine Russia war", 1, "2026年8月31日")

        self.assertIn("Reuters", section_html)
        self.assertIn("2026年8月31日", section_html)
        self.assertIn("https://reuters.com/world/original", section_html)

    def test_bad_original_url_shows_unavailable_link_but_keeps_item(self):
        model_result = (
            '{"items":[{"title_cn":"标题",'
            '"summary_cn":"正文内容。",'
            '"original_url":"https://www.google.com/search?q=test",'
            '"source":"AP"}]}'
        )

        with patch("main.call_gpt_web_rewrite", return_value=model_result):
            section_html = main.process_section("测试", "test", 1, "2026年8月31日")

        self.assertIn("标题", section_html)
        self.assertIn("原文链接不可用", section_html)

    def test_duplicate_titles_are_deduped(self):
        model_result = (
            '{"items":['
            '{"title_cn":"重复标题","summary_cn":"第一条正文。","original_url":"https://reuters.com/a","source":"Reuters"},'
            '{"title_cn":"重复标题","summary_cn":"第二条正文。","original_url":"https://reuters.com/b","source":"Reuters"}'
            ']}'
        )

        with patch("main.call_gpt_web_rewrite", return_value=model_result):
            section_html = main.process_section("测试", "test", 2, "2026年8月31日")

        self.assertEqual(section_html.count("<article"), 1)

    def test_responses_api_enables_web_search(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "output": [
                {
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": '{"items":[]}',
                    }],
                },
            ]
        }

        with patch("main.requests.post", return_value=response) as post:
            output_text = main.call_gpt_web_rewrite("search the article")

        payload = post.call_args.kwargs["json"]
        self.assertEqual(post.call_args.args[0], main.get_responses_url())
        self.assertEqual(payload["tools"], [{"type": "web_search"}])
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(output_text, '{"items":[]}')

    def test_markdown_code_fence_json_is_parsed(self):
        self.assertEqual(
            main._parse_json_response('```json\n{"items":[]}\n```'),
            {"items": []},
        )

    def test_batched_splits_large_target_into_small_requests(self):
        with patch("main.process_section", return_value='<article>x</article>') as ps:
            html = main.process_section_batched("全球综合重大新闻", ["a", "b", "c"], 12, "2026年8月31日")

        self.assertEqual(ps.call_count, 3)
        self.assertEqual(html.count("<article>"), 3)

    def test_shared_seen_titles_dedupes_across_calls(self):
        seen = set()
        m1 = '{"items":[{"title_cn":"共享标题","summary_cn":"第一条正文。","original_url":"https://reuters.com/a","source":"Reuters"}]}'
        m2 = '{"items":[{"title_cn":"共享标题","summary_cn":"第二条正文。","original_url":"https://reuters.com/b","source":"Reuters"}]}'

        with patch("main.call_gpt_web_rewrite", side_effect=[m1, m2]):
            html_one = main.process_section("板块一", "q1", 1, "2026年8月31日", seen)
            html_two = main.process_section("板块二", "q2", 1, "2026年8月31日", seen)

        self.assertEqual(html_one.count("<article"), 1)
        self.assertEqual(html_two.count("<article"), 0)


if __name__ == "__main__":
    unittest.main()
