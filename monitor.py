#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import hashlib
import time
import hmac
import base64
import urllib.parse
from datetime import datetime
import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

BASE_URL = "https://gaj.bozhou.gov.cn/News/showList/6932/"
PAGES = 5
RECORD_FILE = "record.json"

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
    url = BASE_URL + f"page_{page_num}.html"
    print(f"[{datetime.now()}] 正在抓取 {url}")
    with sync_playwright() as p:
        # 启动浏览器，添加隐藏无头特征的参数
        browser = p.firefox.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
            viewport={"width": 1280, "height": 800},
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            }
        )
        page = context.new_page()
        # 添加一个简单的脚本，隐藏 webdriver 属性
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if not response or not response.ok:
            print(f"页面访问失败，状态码: {response.status if response else '无响应'}")
            return []

        # 等待列表容器出现（使用更具体的选择器）
        try:
            page.wait_for_selector(".m-cglist ul", timeout=30000)
        except Exception as e:
            print(f"等待列表超时，页面内容片段：{page.content()[:500]}")
            return []

        # 额外等待 2 秒确保渲染完成
        page.wait_for_timeout(2000)
        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, 'html.parser')
    # 查找包含公告列表的容器
    container = soup.select_one(".m-cglist ul")
    if not container:
        print(f"第{page_num}页未找到列表容器")
        return []

    articles = []
    for item in container.find_all('li'):
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

def fetch_all_articles():
    all_articles = []
    for i in range(1, PAGES + 1):
        page_articles = fetch_articles_from_page(i)
        if page_articles:
            all_articles.extend(page_articles)
        time.sleep(2)
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
