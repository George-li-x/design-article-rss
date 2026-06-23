#!/usr/bin/env python3
"""Generate a daily Chinese-language design-curation RSS feed."""
from __future__ import annotations

import email.utils
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = json.loads((ROOT / "config" / "sources.json").read_text())
SEEN_PATH = ROOT / "data" / "seen.json"
OUTPUT = ROOT / "docs" / "design-rss.xml"
USER_AGENT = "DesignDigestRSS/1.0 (+https://github.com/)"
KEYWORDS = {
    "product": 9, "design": 7, "designer": 6, "ux": 10, "ui": 10,
    "user experience": 10, "interface": 8, "usability": 9, "research": 7,
    "furniture": 9, "interior": 9, "architecture": 7, "industrial": 9,
    "typography": 6, "visual": 5, "service design": 9, "accessibility": 9,
    "interaction": 8, "material": 5, "sustainab": 7, "innovation": 5,
}
RSS_NS = "http://purl.org/rss/1.0/modules/content/"


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    # A dead publisher should not hold the daily job hostage.
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


def text_content(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1] in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def entry_link(node: ET.Element) -> str:
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1] == "link":
            href = child.attrib.get("href")
            if href:
                return href.strip()
            if (child.text or "").strip():
                return child.text.strip()
    return ""


def parse_date(raw: str) -> datetime:
    try:
        value = parsedate_to_datetime(raw)
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)


def parse_feed(source: dict) -> list[dict]:
    raw = get(source["url"])
    root = ET.fromstring(raw)
    entries = [n for n in root.iter() if n.tag.rsplit("}", 1)[-1] in {"item", "entry"}]
    articles = []
    for node in entries[:30]:
        title = text_content(child_text(node, ("title",)))
        link = entry_link(node)
        summary = text_content(child_text(node, ("description", "summary", "encoded", "content")))
        date = parse_date(child_text(node, ("pubDate", "published", "updated", "date")))
        if title and link:
            articles.append({"title": title, "link": link.split("#")[0], "summary": summary[:1200], "date": date, "source": source})
    return articles


def score(article: dict) -> float:
    haystack = (article["title"] + " " + article["summary"]).lower()
    topic_score = sum(weight for term, weight in KEYWORDS.items() if term in haystack)
    # Prefer fresh ideas, without making publication date overwhelm editorial authority.
    age_hours = max(0, (datetime.now(timezone.utc) - article["date"]).total_seconds() / 3600)
    freshness = max(0, 22 - age_hours / 12)
    editorial = 6 if any(term in haystack for term in ("award", "report", "trend", "future", "case study", "research")) else 0
    return article["source"]["authority"] + min(topic_score, 35) + freshness + editorial


def is_chinese(value: str) -> bool:
    return len(re.findall(r"[\u4e00-\u9fff]", value)) >= 3


def translate(value: str) -> str:
    """Translate through Google's no-key web endpoint; return original on failure."""
    if not value or is_chinese(value):
        return value
    try:
        params = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": value[:4500]})
        payload = json.loads(get("https://translate.googleapis.com/translate_a/single?" + params).decode("utf-8"))
        return "".join(part[0] for part in payload[0] if part and part[0]).strip() or value
    except Exception as error:
        print(f"Translation unavailable: {error}")
        return value


def build_item(channel: ET.Element, article: dict) -> None:
    item = ET.SubElement(channel, "item")
    chinese_title = translate(article["title"])
    chinese_summary = translate(article["summary"] or article["title"])
    ET.SubElement(item, "title").text = chinese_title
    ET.SubElement(item, "link").text = article["link"]
    ET.SubElement(item, "guid", isPermaLink="true").text = article["link"]
    ET.SubElement(item, "pubDate").text = email.utils.format_datetime(article["date"])
    description = (
        f"<p><strong>来源：</strong>{html.escape(article['source']['name'])}</p>"
        f"<p>{html.escape(chinese_summary)}</p>"
        f"<p><a href=\"{html.escape(article['link'], quote=True)}\">阅读原文 →</a></p>"
    )
    ET.SubElement(item, "description").text = description
    category = ET.SubElement(item, "category")
    category.text = "设计精选"


def main() -> None:
    seen = set(json.loads(SEEN_PATH.read_text()).get("urls", []))
    candidates = []
    for source in SOURCES:
        try:
            items = parse_feed(source)
            print(f"{source['name']}: {len(items)} articles")
            candidates.extend(item for item in items if item["link"] not in seen)
        except Exception as error:
            print(f"Skipping {source['name']}: {error}")
    candidates.sort(key=score, reverse=True)
    selected = candidates[:10]
    if not selected:
        raise RuntimeError("No new articles found; RSS left unchanged.")

    ET.register_namespace("content", RSS_NS)
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "全球设计文章精选（中文）"
    ET.SubElement(channel, "link").text = "https://github.com/"
    ET.SubElement(channel, "description").text = "每日 10 篇：产品、UI、UX、家具、室内与建筑设计。标题与摘要自动翻译为中文。"
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(datetime.now(timezone.utc))
    for article in selected:
        build_item(channel, article)
    ET.indent(rss, space="  ")
    OUTPUT.parent.mkdir(exist_ok=True)
    ET.ElementTree(rss).write(OUTPUT, encoding="utf-8", xml_declaration=True)
    # Bound the tracked history so the action's state stays small while avoiding repeats for years.
    updated_urls = [item["link"] for item in selected] + list(seen)
    SEEN_PATH.write_text(json.dumps({"urls": updated_urls[:10000]}, ensure_ascii=False, indent=2) + "\n")
    print(f"Published {len(selected)} articles to {OUTPUT}")


if __name__ == "__main__":
    main()
