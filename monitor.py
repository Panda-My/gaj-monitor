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
from bs4 import BeautifulSoup

BASE_URL = "https://gaj.bozhou.gov.cn/News/showList/6932/"
PAGES = 1                     # 只抓取第一页（最新20条）
RECORD_FILE = "record.json"

DINGTALK_WEBHOOK = os.environ.get("DINGTALK_WEBHOOK")
DINGTALK_SECRET = os.environ.get("DINGTALK_SECRET")
if not DINGTALK_WEBHOOK:
    raise RuntimeError("DINGTALK_WEBHOOK environment variable not set")

# 模拟真实浏览器的请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 QQBrowser/21.0.8085.400",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://gaj.bozhou.gov.cn/",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

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
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.encoding = 'utf-8'
        if response.status_code != 200:
            print(f"HTTP {response.status_code} 错误")
            return []
        html = response.text
    except Exception as e:
        print(f"请求失败: {e}")
        return []

    # 检查是否返回了完整页面（包含公告列表）
    if "m-cglist" not in html:
        print("页面中未找到列表容器，可能是反爬")
        return []

    soup = BeautifulSoup(html, 'html.parser')
    container = soup.select_one('.m-cglist ul')
    if not container:
        print("未找到 .m-cglist ul")
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
        time.sleep(2)   # 礼貌延迟
    return all_articles

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
