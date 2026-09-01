import os
import sys
import time
import datetime
import html
import requests
import json
import re
from urllib.parse import urlparse

# Windows 重定向输出常默认为 GBK，统一 UTF-8，避免 emoji 日志掩盖真实异常。
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass

# 所有凭据都从环境变量读取，避免提交到代码仓库。
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-sol").strip()
BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))

def get_beijing_time():
    """获取当前准确的北京时间及前一天日期"""
    now = datetime.datetime.now(BEIJING_TZ)
    yesterday = now - datetime.timedelta(days=1)
    
    today_str = now.strftime("%Y年%m月%d日")
    yesterday_str = yesterday.strftime("%Y年%m月%d日")
    weekday_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][yesterday.weekday()]
    
    return today_str, yesterday_str, weekday_cn


def _parse_quoted_fields(line):
    if '"' not in line:
        raise ValueError("响应中缺少引号字段")
    return line.split('"', 2)[1].split("~" if "~" in line else ",")


def _format_quote_time(value):
    return value.strftime("%Y-%m-%d %H:%M:%S 北京时间")


def _parse_market_datetime(date_value, time_value):
    normalized_date = date_value.strip().replace("-", "")
    return datetime.datetime.strptime(
        f"{normalized_date} {time_value.strip()}", "%Y%m%d %H:%M:%S"
    ).replace(tzinfo=BEIJING_TZ)


def _ensure_fresh_quote(quote_time, max_age_hours):
    age = datetime.datetime.now(BEIJING_TZ) - quote_time
    if age < -datetime.timedelta(minutes=5) or age > datetime.timedelta(hours=max_age_hours):
        raise ValueError(f"报价时间过期或异常：{_format_quote_time(quote_time)}")


def fetch_realtime_market_data():
    """抓取带时间戳的行情；失败时明确不可用，不使用静态兜底数字。"""
    market_info = {
        "gold_intl": "数据暂不可用",
        "gold_cn": "数据暂不可用",
        "sh_index": "上证指数：数据暂不可用",
        "sz_index": "深证成指：数据暂不可用",
        "cy_index": "创业板指：数据暂不可用",
        "stock_updated_at": "未获取",
        "gold_updated_at": "未获取",
        "warnings": [],
    }
    
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    
    # 腾讯完整行情的 30/31/32 字段分别为报价时间、涨跌额、涨跌幅。
    try:
        url_stock = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
        res = requests.get(url_stock, headers=headers, timeout=5)
        if res.status_code != 200:
            raise RuntimeError(f"HTTP {res.status_code}")

        index_map = {
            "v_sh000001": ("sh_index", "上证指数"),
            "v_sz399001": ("sz_index", "深证成指"),
            "v_sz399006": ("cy_index", "创业板指"),
        }
        parsed_indexes = {}
        quote_times = []
        for line in res.text.strip().split(";"):
            identifier = line.split("=", 1)[0].strip()
            if identifier not in index_map:
                continue
            fields = _parse_quoted_fields(line)
            if len(fields) <= 32:
                raise ValueError(f"{identifier} 字段数量不足")
            quote_time = datetime.datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=BEIJING_TZ)
            _ensure_fresh_quote(quote_time, max_age_hours=96)
            key, name = index_map[identifier]
            parsed_indexes[key] = f"{name}：{float(fields[3]):.2f} 点（{float(fields[32]):+.2f}%）"
            quote_times.append(quote_time)

        if len(quote_times) != 3 or len(parsed_indexes) != 3:
            raise ValueError("指数行情不完整")
        market_info.update(parsed_indexes)
        market_info["stock_updated_at"] = _format_quote_time(max(quote_times))
    except Exception as exc:
        warning = f"A 股行情获取失败：{exc}"
        market_info["warnings"].append(warning)
        print(f"{warning}")

    # 新浪金价的 0/6/12 字段分别为现价、报价时间、报价日期。
    try:
        url_gold = "https://hq.sinajs.cn/list=hf_GC,gds_AUTD"
        res = requests.get(url_gold, headers=headers, timeout=5)
        if res.status_code != 200:
            raise RuntimeError(f"HTTP {res.status_code}")

        parsed_gold = {}
        gold_times = []
        for line in res.text.strip().split(";"):
            if "hf_GC" in line and '"' in line:
                fields = _parse_quoted_fields(line)
                quote_time = _parse_market_datetime(fields[12], fields[6])
                _ensure_fresh_quote(quote_time, max_age_hours=96)
                parsed_gold["gold_intl"] = f"{float(fields[0]):.2f} 美元/盎司"
                gold_times.append(quote_time)
            elif "gds_AUTD" in line and '"' in line:
                fields = _parse_quoted_fields(line)
                quote_time = _parse_market_datetime(fields[12], fields[6])
                _ensure_fresh_quote(quote_time, max_age_hours=96)
                parsed_gold["gold_cn"] = f"{float(fields[0]):.2f} 元/克"
                gold_times.append(quote_time)

        if len(gold_times) != 2 or len(parsed_gold) != 2:
            raise ValueError("黄金行情不完整")
        market_info.update(parsed_gold)
        market_info["gold_updated_at"] = _format_quote_time(max(gold_times))
    except Exception as exc:
        warning = f"黄金行情获取失败：{exc}"
        market_info["warnings"].append(warning)
        print(f"{warning}")

    return market_info

def get_responses_url():
    """兼容填写站点根地址、/v1 地址或原 Chat Completions 地址。"""
    base_url = OPENAI_BASE_URL.rstrip("/")
    if base_url.endswith("/responses"):
        return base_url
    if base_url.endswith("/chat/completions"):
        return base_url.removesuffix("/chat/completions") + "/responses"
    if base_url.endswith("/v1"):
        return f"{base_url}/responses"
    return f"{base_url}/v1/responses"


_SEARCH_PAGE_HOSTS = {"google.com", "news.google.com", "bing.com"}


def _usable_original_url(url, fallback):
    """只做链接卫生检查：拒绝搜索结果页与非法 URL，异常时回退到 RSS 链接。"""
    url = (url or "").strip()
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    except (TypeError, ValueError):
        return fallback
    if parsed.scheme not in {"http", "https"} or not hostname:
        return fallback
    if hostname in _SEARCH_PAGE_HOSTS:
        return fallback
    return url


def _parse_json_response(text):
    """容忍 Markdown 代码围栏，但拒绝无法解析的自由文本结果。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text, count=1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型未返回 JSON 对象")
    return json.loads(text[start:end + 1])


def _extract_response_text_and_sources(data):
    text_parts = []
    source_urls = set()
    if isinstance(data.get("output_text"), str):
        text_parts.append(data["output_text"])

    def collect_urls(value):
        if isinstance(value, dict):
            if isinstance(value.get("url"), str):
                source_urls.add(value["url"])
            for nested in value.values():
                collect_urls(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_urls(nested)

    collect_urls(data)
    for output in data.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                if isinstance(content.get("text"), str):
                    text_parts.append(content["text"])

    output_text = "\n".join(dict.fromkeys(text_parts)).strip()
    if not output_text:
        raise ValueError("Responses API 未返回 output_text")
    return output_text, source_urls


def call_gpt_web_rewrite(prompt):
    """通过 Responses API 让模型联网检索并阅读媒体原文后缩写。"""
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_MODEL,
        "instructions": (
            "你是一名严谨的中文国际新闻编辑。对每条候选必须使用 web_search 搜索"
            "并打开媒体原文阅读正文，再依据正文撰写，不得只看标题、不得虚构原文之外的事实。"
        ),
        "input": prompt,
        "tools": [{"type": "web_search"}],
        "tool_choice": "required",
        "include": ["web_search_call.action.sources"],
    }

    last_error = "未知错误"
    for attempt in range(3):
        try:
            response = requests.post(
                get_responses_url(),
                headers=headers,
                json=payload,
                timeout=240,
            )
            if response.status_code == 200:
                return _extract_response_text_and_sources(response.json())

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            if response.status_code != 429 and response.status_code < 500:
                break
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            last_error = str(exc)

        if attempt < 2:
            time.sleep(2 ** attempt)

    raise RuntimeError(f"调用 {OPENAI_MODEL} 联网改写失败：{last_error}")


def process_section(section_name, search_query, target_count, yesterday_str):
    """让模型用 web_search 自主搜索并撰写；来源由模型返回，日期由程序填写。"""
    output_count = max(int(target_count), 1)
    print(f"正在撰写板块：{section_name}（目标 {output_count} 条）...")

    prompt = f"""
请使用 web_search 搜索并阅读 {yesterday_str}（北京时间）发布、与【{section_name}】相关的权威媒体（Reuters/AP/BBC 等）新闻，选择恰好 {output_count} 条最重要且互不重复的新闻逐条撰写。搜索建议关键词：{search_query}

每条新闻的撰写规范如下：
1. 标题（title_cn）：两段式短句，中间用一个空格隔开，总字数不超过14字，单段不超过8字，透露出60%-75%的核心信息，给正文留出展开空间。
2. 正文（summary_cn）：严格控制在130-160字（上限170字），3-4句，逗号分句尽量不超过22字：
   - 第一句：直接给出最硬的事实，交代时间、地点、人物与核心动作，补全标题未说全的信息。
   - 第二句：补清当前变化、人物关系、冲突双方或关键状态。
   - 第三句：补清起因、机制、必要的前后变化或直接原因。
   - 第四句（若有）：交代目前进展、分歧或具体结果，停在具体事实上，绝不作主观评论，不使用"这说明/意味着/值得注意"等套话。
3. 必须包含新闻六要素（时间、地点、人物、起因、经过、结果），缺一不可。
4. original_url 填写你在 web_search 中打开的媒体原站文章 URL，不得返回 Google News 或搜索结果页。
5. source 填写媒体名称（如 Reuters、AP、BBC）。
6. 排除涉及中国国家领导人的报道。
7. 只返回 JSON，不要 Markdown：
{{"items":[{{"title_cn":"第一段 第二段","summary_cn":"正文","original_url":"https://媒体原站/文章","source":"媒体名"}}]}}
    """

    try:
        response_text, _ = call_gpt_web_rewrite(prompt)
        result = _parse_json_response(response_text)
    except (RuntimeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"{section_name} 搜索或结果解析失败：{exc}")
        return '<p style="color:#888;">本板块暂未获得可验证的新闻。</p>'

    rewritten_items = result.get("items")
    if not isinstance(rewritten_items, list):
        print(f"{section_name} 的模型结果缺少 items 数组")
        return '<p style="color:#888;">本板块暂未获得可验证的新闻。</p>'

    html_items = []
    seen_titles = set()
    for rewritten in rewritten_items:
        if not isinstance(rewritten, dict):
            continue
        title_cn = str(rewritten.get("title_cn", "")).strip()
        summary_cn = str(rewritten.get("summary_cn", "")).strip()
        source = str(rewritten.get("source", "")).strip() or "权威媒体"
        if not title_cn or not summary_cn or len(title_cn) > 40 or len(summary_cn) > 220:
            continue
        title_key = re.sub(r"\s+", " ", title_cn).casefold()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        original_url = _usable_original_url(str(rewritten.get("original_url", "")), "")
        safe_title = html.escape(title_cn)
        safe_summary = html.escape(summary_cn)
        safe_source = html.escape(source)
        safe_date = html.escape(yesterday_str)
        safe_link = html.escape(original_url, quote=True)
        link_html = f'<a href="{safe_link}">查看原始报道</a>' if safe_link else "原文链接不可用"
        html_items.append(f"""
        <article style="margin:0 0 16px 0;">
          <strong>{safe_title}</strong><br>
          <span>{safe_summary}</span><br>
          <small style="color:#777;">来源：{safe_source}｜{safe_date}｜{link_html}</small>
        </article>
        """)
        if len(html_items) >= output_count:
            break

    if not html_items:
        print(f"{section_name} 没有有效撰写结果")
        return '<p style="color:#888;">本板块暂未获得可验证的新闻。</p>'
    return "".join(html_items)

def build_final_html(today_str, yesterday_str, sections_data, market_data):
    """流水线组装：将所有写好的板块与真实金融行情拼接为完整 HTML"""
    html_parts = []
    
    html_parts.append(f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #222; max-width: 800px; margin: 0 auto; padding: 15px;">
      <h2 style="color: #1a73e8; border-bottom: 2px solid #1a73e8; padding-bottom: 8px; margin-bottom: 6px;">每日新闻简报</h2>
    """)

    color_map = {
        "一、俄乌冲突与前线战况": "#1a73e8",
        "二、美以伊局势与中东战事": "#d93025",
        "三、人工智能与大模型前沿": "#188038",
        "四、全球综合重大新闻": "#e37400"
    }

    for title, content in sections_data.items():
        border_color = color_map.get(title, "#1a73e8")
        html_parts.append(f"""
        <div style="margin-top: 20px;">
          <h3 style="background-color: #f1f3f4; color: {border_color}; padding: 6px 12px; border-left: 4px solid {border_color}; margin-bottom: 12px; font-size: 15px;">{title}</h3>
          <div style="font-size: 14px; color: #333; line-height: 1.7;">{content}</div>
        </div>
        """)

    # 拼接文末金融行情
    html_parts.append(f"""
      <div style="margin-top: 25px; padding: 14px; background-color: #f8f9fa; border: 1px solid #e0e0e0; border-radius: 6px;">
        <h3 style="color: #202124; margin: 0 0 10px 0; font-size: 15px;">市场行情速览（带报价时间）</h3>
        <p style="margin: 4px 0; font-size: 13px; color: #333;"><strong>黄金报价</strong>：</p>
        <ul style="margin: 4px 0 10px 20px; font-size: 13px; color: #555; padding-left: 0;">
          <li>上海黄金交易所 Au(T+D)：<strong>{market_data['gold_cn']}</strong></li>
          <li>COMEX 黄金期货（GC）：<strong>{market_data['gold_intl']}</strong></li>
        </ul>
        <p style="margin: 2px 0 10px 0; font-size: 12px; color: #777;">黄金报价时间：{market_data['gold_updated_at']}</p>
        <p style="margin: 4px 0; font-size: 13px; color: #333;"><strong>A 股最近有效行情</strong>：</p>
        <ul style="margin: 4px 0 6px 20px; font-size: 13px; color: #555; padding-left: 0;">
          <li>{market_data['sh_index']}</li>
          <li>{market_data['sz_index']}</li>
          <li>{market_data['cy_index']}</li>
        </ul>
        <p style="margin: 2px 0 6px 0; font-size: 12px; color: #777;">A 股报价时间：{market_data['stock_updated_at']}</p>
        <p style="margin: 6px 0 2px 0; font-size: 11px; color: #999;">数据源：腾讯行情（A 股指数）、新浪财经行情（GC、Au(T+D)）。非交易时段显示最近有效报价。</p>
        {''.join(f'<p style="margin:3px 0;color:#d93025;font-size:12px;">{html.escape(warning)}</p>' for warning in market_data['warnings'])}
      </div>
      <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 25px 0 12px 0;">
    </div>
    """)
    
    return "".join(html_parts)

def send_to_pushplus(html_content):
    today_str, _, _ = get_beijing_time()
    title = f"每日新闻简报 - {today_str}"
    
    url = "https://www.pushplus.plus/send"
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": html_content,
        "template": "html"
    }
    resp = requests.post(url, json=data, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"PushPlus 推送失败：HTTP {resp.status_code}")
    print("PushPlus 微信推送响应:", resp.text)

def main():
    missing_vars = [
        name
        for name, value in {
            "OPENAI_API_KEY": OPENAI_API_KEY,
            "OPENAI_BASE_URL": OPENAI_BASE_URL,
            "PUSHPLUS_TOKEN": PUSHPLUS_TOKEN,
        }.items()
        if not value
    ]
    if missing_vars:
        print(f"错误：缺少环境变量：{', '.join(missing_vars)}")
        sys.exit(1)

    today_str, yesterday_str, _ = get_beijing_time()
    print(f"启动自动化流水线：今日基准 {today_str}，目标新闻日期 {yesterday_str}，模型 {OPENAI_MODEL}")

    # 1. 获取当日金价与 A 股行情
    print("正在获取当日金价与 A 股行情...")
    market_data = fetch_realtime_market_data()

    # 2. 让模型自主搜索并撰写新闻（世界板块作为弹性填充，确保总数达到 20 条）
    sections_data = {}
    sections_data["一、俄乌冲突与前线战况"] = process_section("俄乌冲突与前线战况", "Ukraine Russia war", 4, yesterday_str)
    sections_data["二、美以伊局势与中东战事"] = process_section("美以伊局势与中东战事", "Israel Iran Gaza", 3, yesterday_str)
    sections_data["三、人工智能与大模型前沿"] = process_section("人工智能与大模型前沿", "artificial intelligence OpenAI Anthropic", 3, yesterday_str)

    done_count = sum(s.count("<article") for s in sections_data.values())
    world_target = max(20 - done_count, 0)
    sections_data["四、全球综合重大新闻"] = process_section("全球综合重大新闻", "world news", world_target, yesterday_str)

    total_articles = sum(s.count("<article") for s in sections_data.values())
    print(f"本次共生成新闻 {total_articles} 条（目标 20 条）")

    # 3. 组装并推送
    print("正在组装新闻简报与金融行情 HTML...")
    final_html = build_final_html(today_str, yesterday_str, sections_data, market_data)

    print("正在推送到微信...")
    send_to_pushplus(final_html)
    print("流水线执行成功，微信已成功收到推送！")

if __name__ == "__main__":
    main()
