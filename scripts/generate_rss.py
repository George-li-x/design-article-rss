#!/usr/bin/env python3
"""Create a daily, full-text Chinese design RSS feed.

The generated feed is a deploy artifact, not a long-lived article archive.  The
repository keeps only a compact publication manifest and de-duplication state.
"""
from __future__ import annotations

import email.utils
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    import trafilatura
except ImportError:  # Makes syntax and feed parsing checks possible without dependencies.
    trafilatura = None

ROOT = Path(__file__).resolve().parents[1]
SOURCES = json.loads((ROOT / "config" / "sources.json").read_text())
SEEN_PATH = ROOT / "data" / "seen.json"
ARCHIVE_PATH = ROOT / "data" / "published.json"
OUTPUT = ROOT / "docs" / "design-rss.xml"
USER_AGENT = "DesignDigestRSS/2.0 (+https://github.com/George-li-x/design-article-rss)"
CHINESE_PER_DAY = 4
ARCHIVE_RETENTION_DAYS = 180
MAX_TRANSLATION_CHARS = 3200
KEYWORDS = {
    "product": 9, "design": 7, "designer": 6, "ux": 10, "ui": 10,
    "user experience": 10, "interface": 8, "usability": 9, "research": 7,
    "furniture": 9, "interior": 9, "architecture": 7, "industrial": 9,
    "typography": 6, "visual": 5, "service design": 9, "accessibility": 9,
    "interaction": 8, "material": 5, "sustainab": 7, "innovation": 5,
    "产品": 9, "设计": 7, "用户体验": 10, "界面": 8, "交互": 8,
    "家具": 9, "室内": 9, "建筑": 7, "无障碍": 9, "服务设计": 9,
    "可持续": 7, "调研": 7,
}
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
MEDIA_NS = "http://search.yahoo.com/mrss/"


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xml,text/xml,*/*"})
    with urllib.request.urlopen(request, timeout=15) as response:
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
            if child.attrib.get("href"):
                return child.attrib["href"].strip()
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
    root = ET.fromstring(get(source["url"]))
    entries = [n for n in root.iter() if n.tag.rsplit("}", 1)[-1] in {"item", "entry"}]
    articles = []
    for node in entries[:40]:
        title = text_content(child_text(node, ("title",)))
        link = entry_link(node)
        summary = text_content(child_text(node, ("description", "summary", "encoded", "content")))
        date = parse_date(child_text(node, ("pubDate", "published", "updated", "date")))
        if title and link:
            articles.append({"title": title, "link": link.split("#")[0], "summary": summary[:2000], "date": date, "source": source})
    return articles


def score(article: dict) -> float:
    haystack = (article["title"] + " " + article["summary"]).lower()
    topic_score = sum(weight for term, weight in KEYWORDS.items() if term in haystack)
    age_hours = max(0, (datetime.now(timezone.utc) - article["date"]).total_seconds() / 3600)
    freshness = max(0, 22 - age_hours / 12)
    editorial = 6 if any(term in haystack for term in ("award", "report", "trend", "future", "case study", "research", "趋势", "案例", "报告")) else 0
    return article["source"]["authority"] + min(topic_score, 35) + freshness + editorial


def is_chinese(value: str) -> bool:
    return len(re.findall(r"[\u4e00-\u9fff]", value)) >= 3


def split_for_translation(value: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", value) if part.strip()]
    chunks, current = [], ""
    for paragraph in paragraphs:
        if len(paragraph) > MAX_TRANSLATION_CHARS:
            sentences = re.split(r"(?<=[.!?。！？])\s+", paragraph)
        else:
            sentences = [paragraph]
        for sentence in sentences:
            if current and len(current) + len(sentence) + 2 > MAX_TRANSLATION_CHARS:
                chunks.append(current)
                current = ""
            current = f"{current}\n\n{sentence}".strip()
    if current:
        chunks.append(current)
    return chunks or [value]


def translate(value: str) -> str:
    """Translate text to Chinese through Google's no-key endpoint; keep original on failure."""
    if not value or is_chinese(value):
        return value
    translated = []
    for chunk in split_for_translation(value):
        try:
            params = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": chunk})
            payload = json.loads(get("https://translate.googleapis.com/translate_a/single?" + params).decode("utf-8"))
            translated.append("".join(part[0] for part in payload[0] if part and part[0]).strip() or chunk)
        except Exception as error:
            print(f"Translation unavailable: {error}")
            translated.append(chunk)
    return "\n\n".join(translated)


def meta_value(page: str, property_name: str) -> str:
    for tag in re.findall(r"<meta\b[^>]*>", page, flags=re.I):
        attributes = dict((name.lower(), value) for name, _, value in re.findall(r"([\w:-]+)\s*=\s*(['\"])(.*?)\2", tag, flags=re.I | re.S))
        if attributes.get("property", "").lower() == property_name or attributes.get("name", "").lower() == property_name:
            return html.unescape(attributes.get("content", "")).strip()
    return ""


def fetch_full_article(article: dict) -> dict:
    """Fetch primary article text plus the canonical social image, falling back to feed text."""
    fallback = article["summary"] or article["title"]
    try:
        page = get(article["link"]).decode("utf-8", errors="replace")
        image = meta_value(page, "og:image")
        extracted = trafilatura.extract(page, include_comments=False, include_tables=True, favor_precision=True) if trafilatura else None
        body = text_content(extracted or "")
        if len(body) < 300:
            body = fallback
        article.update({"body": body[:30000], "image": image, "full_text": len(body) >= 300})
    except Exception as error:
        print(f"Full-text fetch unavailable for {article['link']}: {error}")
        article.update({"body": fallback, "image": "", "full_text": False})
    return article


def select_articles(candidates: list[dict]) -> list[dict]:
    candidates.sort(key=score, reverse=True)
    chinese = [item for item in candidates if item["source"].get("language") == "zh" or is_chinese(item["title"])]
    international = [item for item in candidates if item not in chinese]
    selected = chinese[:CHINESE_PER_DAY] + international[:10 - CHINESE_PER_DAY]
    if len(selected) < 10:
        selected += [item for item in candidates if item not in selected][:10 - len(selected)]
    return sorted(selected, key=score, reverse=True)[:10]


def description_html(article: dict, chinese_title: str, chinese_body: str) -> str:
    paragraphs = [text_content(part) for part in re.split(r"\n{2,}", chinese_body) if text_content(part)]
    image = f'<figure><img src="{html.escape(article["image"], quote=True)}" alt="{html.escape(chinese_title, quote=True)}" /></figure>' if article["image"] else ""
    body = "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)
    status = "已抓取正文并翻译" if article["full_text"] else "正文抓取受限，以下为来源摘要"
    return (
        f"<p><strong>来源：</strong>{html.escape(article['source']['name'])}　<strong>处理：</strong>{status}</p>"
        f"{image}<h3>中文全文</h3>{body}"
        f"<p><a href=\"{html.escape(article['link'], quote=True)}\">阅读原文 →</a></p>"
    )


def build_item(channel: ET.Element, article: dict) -> dict:
    item = ET.SubElement(channel, "item")
    chinese_title = translate(article["title"])
    chinese_body = translate(article["body"])
    content = description_html(article, chinese_title, chinese_body)
    ET.SubElement(item, "title").text = chinese_title
    ET.SubElement(item, "link").text = article["link"]
    ET.SubElement(item, "guid", isPermaLink="true").text = article["link"]
    ET.SubElement(item, "pubDate").text = email.utils.format_datetime(article["date"])
    ET.SubElement(item, "description").text = content
    ET.SubElement(item, f"{{{CONTENT_NS}}}encoded").text = content
    ET.SubElement(item, "category").text = "中文设计全文"
    if article["image"]:
        ET.SubElement(item, f"{{{MEDIA_NS}}}content", url=article["image"], medium="image")
    return {"url": article["link"], "title": chinese_title, "source": article["source"]["name"], "language": article["source"].get("language", "unknown"), "published_at": datetime.now(timezone.utc).isoformat(), "source_date": article["date"].isoformat(), "full_text": article["full_text"]}


def load_json(path: Path, default: dict) -> dict:
    return json.loads(path.read_text()) if path.exists() else default


def update_state(selected: list[dict], manifest: list[dict]) -> None:
    prior_seen = load_json(SEEN_PATH, {"urls": []}).get("urls", [])
    urls = [article["link"] for article in selected] + prior_seen
    SEEN_PATH.write_text(json.dumps({"urls": list(dict.fromkeys(urls))[:10000]}, ensure_ascii=False, indent=2) + "\n")
    existing = load_json(ARCHIVE_PATH, {"articles": []}).get("articles", [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_RETENTION_DAYS)
    retained = []
    for item in manifest + existing:
        try:
            if datetime.fromisoformat(item["published_at"]) >= cutoff and item["url"] not in {entry["url"] for entry in retained}:
                retained.append(item)
        except (KeyError, ValueError):
            continue
    ARCHIVE_PATH.write_text(json.dumps({"retention_days": ARCHIVE_RETENTION_DAYS, "articles": retained[:2000]}, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    seen = set(load_json(SEEN_PATH, {"urls": []}).get("urls", []))
    candidates = []
    for source in SOURCES:
        try:
            items = parse_feed(source)
            print(f"{source['name']}: {len(items)} articles")
            candidates.extend(item for item in items if item["link"] not in seen)
        except Exception as error:
            print(f"Skipping {source['name']}: {error}")
    selected = select_articles(candidates)
    if not selected:
        raise RuntimeError("No new articles found; RSS left unchanged.")
    selected = [fetch_full_article(article) for article in selected]

    ET.register_namespace("content", CONTENT_NS)
    ET.register_namespace("media", MEDIA_NS)
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "全球设计文章精选（中文全文版）"
    ET.SubElement(channel, "link").text = "https://george-li-x.github.io/design-article-rss/"
    ET.SubElement(channel, "description").text = "每日 10 篇设计文章：4 篇中文社区文章、6 篇国际文章的中文全文版，含首图和原文链接。"
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(datetime.now(timezone.utc))
    manifest = [build_item(channel, article) for article in selected]
    ET.indent(rss, space="  ")
    OUTPUT.parent.mkdir(exist_ok=True)
    ET.ElementTree(rss).write(OUTPUT, encoding="utf-8", xml_declaration=True)
    update_state(selected, manifest)
    chinese_count = sum(1 for article in selected if article["source"].get("language") == "zh")
    print(f"Published {len(selected)} articles ({chinese_count} Chinese sources) to {OUTPUT}")


if __name__ == "__main__":
    main()
