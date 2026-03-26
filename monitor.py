#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import hashlib
import time
import hmac
import base64
import urllib.parse
import random
from datetime import datetime
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup

BASE_URL = "https://gaj.bozhou.gov.cn/News/showList/6932/"
PAGES = 1                     # 只抓取第一页（最新20条）
RECORD_FILE = "record.json"
MAX_RETRIES = 3               # 每页最多重试3次
RETRY_DELAY = 5               # 重试等待秒数

DINGTALK_WEBHOOK = os.environ.get("DINGTALK_WEBHOOK")
DINGTALK_SECRET = os.environ.get("DINGTALK_SECRET")
if not DINGTALK_WEBHOOK:
    raise RuntimeError("DINGTALK_WEBHOOK environment variable not set")

def sign_dingtalk(secret, timestamp):
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(secret.encode(), string_to_sign.encode(), digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return sign

def send_dingtalk_message(content):
    headers = {"Content-Type": "application/json"}
    data = {"msgtype": "text", "text": {"content": content}}
    url = DINGTALK_WEBHOOK
    if DINGTALK_SECRET:
        timestamp = str(round(time.time() * 1000))
        sign = sign_dingtalk(DINGTALK_SECRET, timestamp)
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        query.update({"timestamp": timestamp, "sign": sign})
        new_query = urllib.parse.urlencode(query)
        url = urllib.parse.urlunparse(parsed._replace(query=new_query))

    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        print(f"[{datetime.now()}] 推送成功: {content[:50]}...")
    except Exception as e:
        print(f"[{datetime.now()}] 推送失败: {e}")

def load_records():
    if os.path.exists(RECORD_FILE):
        with open(RECORD_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_records(records):
    records.sort(key=lambda x: x.get('date', ''), reverse=True)
    records = records[:100]
    with open(RECORD_FILE, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

def fetch_articles_from_page(page_num):
    """带重试的抓取函数"""
    url = BASE_URL + f"page_{page_num}.html"
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[{datetime.now()}] 尝试 {attempt}/{MAX_RETRIES} 抓取 {url}")
        try:
            with sync_playwright() as p:
                # 使用 firefox，增加更真实的浏览器特征
                browser = p.firefox.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0",
                    viewport={"width": 1920, "height": 1080},
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    extra_http_headers={
                        "Accept-Language": "zh-CN,zh;q=0.9",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    }
                )
                page = context.new_page()
                # 添加随机延迟，模拟人类行为
                time.sleep(random.uniform(1, 3))
                response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if not response or not response.ok:
                    print(f"页面访问失败，状态码: {response.status if response else '无响应'}")
                    continue

                # 等待页面中必要的元素出现（可能是 ul 或 .m-cglist）
                try:
                    # 优先等待 ul 元素
                    page.wait_for_selector("ul", timeout=20000)
                except PlaywrightTimeoutError:
                    # 如果 ul 超时，尝试等待 .m-cglist 容器
                    try:
                        page.wait_for_selector(".m-cglist", timeout=10000)
                    except PlaywrightTimeoutError:
                        print(f"等待列表容器超时，页面内容片段：{page.content()[:500]}")
                        # 如果仍然超时，则重试
                        continue

                # 额外等待动态内容填充
                page.wait_for_timeout(2000)
                html = page.content()
                browser.close()

            soup = BeautifulSoup(html, 'html.parser')
            # 尝试多种选择器
            ul = soup.find('ul')
            if not ul:
                # 尝试找 class 包含 cglist 的容器
                ul = soup.select_one('.m-cglist ul')
            if not ul:
                print(f"第{page_num}页未找到列表容器")
                continue

            articles = []
            for item in ul.find_all('li'):
                a_tag = item.find('a')
                span_tag = item.find('span')
                if not a_tag:
                    continue
                title = a_tag.get_text(strip=True)
                link = a_tag.get('href')
                if not title or not link:
                    continue
                if link.startswith('/'):
                    link = "https://gaj.bozhou.gov.cn" + link
                date = span_tag.get_text(strip=True) if span_tag else ""
                unique_id = hashlib.md5(f"{title}{link}".encode()).hexdigest()
                articles.append({
                    "id": unique_id,
                    "title": title,
                    "link": link,
                    "date": date
                })
            print(f"第{page_num}页抓到 {len(articles)} 条公告")
            return articles

        except Exception as e:
            print(f"抓取过程中出现异常: {e}")
            if attempt < MAX_RETRIES:
                print(f"等待 {RETRY_DELAY} 秒后重试...")
                time.sleep(RETRY_DELAY)
            else:
                print("已达到最大重试次数，放弃抓取")
                return []

    return []

def fetch_all_articles():
    all_articles = []
    for i in range(1, PAGES + 1):
        page_articles = fetch_articles_from_page(i)
        if page_articles:
            all_articles.extend(page_articles)
        time.sleep(random.uniform(3, 5))  # 随机延迟
    # 去重
    seen = set()
    unique = []
    for art in all_articles:
        if art['id'] not in seen:
            seen.add(art['id'])
            unique.append(art)
    unique.sort(key=lambda x: x.get('date', ''), reverse=True)
    return unique

def main():
    print(f"[{datetime.now()}] 开始监控（抓取前{PAGES}页）")
    existing = load_records()
    existing_ids = {r['id'] for r in existing}

    articles = fetch_all_articles()
    if not articles:
        print("未抓取到任何文章，请检查网络或网页结构")
        return

    new_articles = [a for a in articles if a['id'] not in existing_ids]
    if new_articles:
        print(f"发现 {len(new_articles)} 条新内容")
        for art in new_articles:
            msg = f"【新增公告】\n标题：{art['title']}\n链接：{art['link']}"
            if art['date']:
                msg += f"\n日期：{art['date']}"
            send_dingtalk_message(msg)
            time.sleep(1)

        all_records = existing + new_articles
        all_records.sort(key=lambda x: x.get('date', ''), reverse=True)
        all_records = all_records[:100]
        save_records(all_records)
        print(f"记录已更新，当前共 {len(all_records)} 条")
    else:
        print("无新增内容")

if __name__ == "__main__":
    main()
