import datetime
import sys
import types
import unittest
from email.utils import format_datetime
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


class DataFreshnessTests(unittest.TestCase):
    def test_rss_rejects_items_older_than_24_hours(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        recent = format_datetime(now - datetime.timedelta(hours=2))
        stale = format_datetime(now - datetime.timedelta(hours=48))
        rss = f"""<?xml version="1.0" encoding="UTF-8"?>
        <rss><channel>
          <item><title>Recent report</title><source>Reuters</source><pubDate>{recent}</pubDate><link>https://example.com/recent</link></item>
          <item><title>Stale report</title><source>Reuters</source><pubDate>{stale}</pubDate><link>https://example.com/stale</link></item>
        </channel></rss>"""

        with patch("main.requests.get", return_value=FakeResponse(rss)):
            items = main.fetch_rss_news("test", limit=5)

        self.assertEqual([item["title"] for item in items], ["Recent report"])
        self.assertEqual(items[0]["link"], "https://example.com/recent")

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
            f'var hq_str_hf_GC="4480.81,0,0,0,0,0,13:32:12,0,0,0,0,0,{quote_date},纽约黄金";\n'
            f'var hq_str_gds_AUTD="958.37,0,0,0,0,0,13:32:12,0,0,0,0,0,{quote_date},黄金延期";'
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
            f'var hq_str_hf_GC="4480.81,0,0,0,0,0,13:32:12,0,0,0,0,0,{quote_date},纽约黄金";\n'
            f'var hq_str_gds_AUTD="958.37,0,0,0,0,0,13:32:12,0,0,0,0,0,{quote_date},黄金延期";'
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
            f'v_sz399006="51~创业板指~399006~3408.14~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~{quote_time}~-30.54~-0.89";'
        )
        gold = (
            f'var hq_str_hf_GC="4480.81,0,0,0,0,0,13:32:12,0,0,0,0,0,{quote_date},纽约黄金";\n'
            f'var hq_str_gds_AUTD="958.37,0,0,0,0,0,13:32:12,0,0,0,0,0,{quote_date},黄金延期";'
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

    def test_program_owns_source_time_and_link_metadata(self):
        raw_items = [{
            "title": "Original headline",
            "source": "Reuters",
            "source_url": "https://www.reuters.com",
            "pub_date": "2026-08-31 20:30 北京时间",
            "link": "https://news.google.com/rss/articles/example",
        }]
        model_result = (
            '{"items":[{"id":0,"title_cn":"测试标题",'
            '"summary_cn":"这是模型联网读取媒体正文后生成的测试缩写。",'
            '"source":"伪造媒体","original_url":"https://reuters.com/world/original?a=1&b=2"}]}'
        )

        def fake_gpt(prompt):
            self.assertIn("Original headline", prompt)
            self.assertIn("google_news_url", prompt)
            return model_result, {"https://reuters.com/world/original?a=1&b=2"}

        with patch("main.call_gpt_web_rewrite", side_effect=fake_gpt):
            section_html = main.process_section("测试", raw_items, 1, "2026年08月31日")

        self.assertIn("Reuters", section_html)
        self.assertIn("2026-08-31 20:30 北京时间", section_html)
        self.assertIn("https://reuters.com/world/original?a=1&amp;b=2", section_html)
        self.assertNotIn("伪造媒体", section_html)

    def test_responses_api_enables_web_search_and_collects_citations(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [{"url": "https://reuters.com/world/original"}]
                    },
                },
                {
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": '{"items":[]}',
                        "annotations": [{
                            "type": "url_citation",
                            "url": "https://reuters.com/world/original",
                        }],
                    }],
                },
            ]
        }

        with patch("main.requests.post", return_value=response) as post:
            output_text, sources = main.call_gpt_web_rewrite("search the article")

        payload = post.call_args.kwargs["json"]
        self.assertEqual(post.call_args.args[0], main.get_responses_url())
        self.assertEqual(payload["tools"], [{"type": "web_search"}])
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(output_text, '{"items":[]}')
        self.assertIn("https://reuters.com/world/original", sources)

    def test_citation_tracking_parameters_do_not_reject_valid_media_url(self):
        self.assertTrue(
            main._is_cited_original_url(
                "https://www.reuters.com/world/article-123?utm_source=search",
                "https://www.reuters.com",
                {"https://reuters.com/world/article-123?ref=web_search"},
            )
        )

    def test_uncited_original_url_is_rejected(self):
        raw_items = [{
            "title": "Original headline",
            "source": "Reuters",
            "source_url": "https://www.reuters.com",
            "pub_date": "2026-08-31 20:30 北京时间",
            "link": "https://news.google.com/rss/articles/example",
        }]
        model_result = (
            '{"items":[{"id":0,"title_cn":"测试标题",'
            '"summary_cn":"这段文字声称来自正文但没有匹配的联网引用。",'
            '"original_url":"https://fake.example/article"}]}'
        )

        with patch(
            "main.call_gpt_web_rewrite",
            return_value=(model_result, {"https://fake.example/article"}),
        ):
            section_html = main.process_section("测试", raw_items, 1, "2026年08月31日")

        self.assertIn("暂未获得可验证", section_html)


if __name__ == "__main__":
    unittest.main()
