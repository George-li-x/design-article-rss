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
ARTICLES_PER_DAY = 20
CHINESE_PER_DAY = 8
ARCHIVE_RETENTION_DAYS = 180
MAX_TRANSLATION_CHARS = 3200
MEDIUM_AUTHOR_FEEDS: dict[str, dict[str, str]] = {}
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
        # Medium and several WordPress publications put the real article body in
        # content:encoded. Preserve that rich HTML as a first-class fallback.
        feed_html = child_text(node, ("encoded", "content"))
        summary = text_content(feed_html or child_text(node, ("summary", "description")))
        date = parse_date(child_text(node, ("pubDate", "published", "updated", "date")))
        if title and link:
            articles.append({"title": title, "link": link.split("#")[0], "summary": summary[:2000], "feed_html": feed_html, "date": date, "source": source})
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
    """Fetch semantic article HTML and every article image, falling back to feed text."""
    fallback = article["summary"] or article["title"]
    feed_body = sanitize_feed_html(article.get("feed_html", ""))
    # Medium's official feed normally includes the full article while the web
    # page often presents a consent/login or anti-bot wall to automated fetches.
    if article["source"]["name"].startswith("Medium"):
        author_body = medium_author_article_body(article["link"])
        if len(text_content(author_body)) > len(text_content(feed_body)):
            feed_body = author_body
    if article["source"]["name"].startswith("Medium") and len(text_content(feed_body)) >= 500:
        article.update({
            "body_html": feed_body,
            "image": first_image(feed_body),
            "full_text": True,
        })
        return article
    try:
        page = get(article["link"]).decode("utf-8", errors="replace")
        image = meta_value(page, "og:image")
        extracted = trafilatura.extract(
            page, output_format="xml", include_comments=False, include_tables=True,
            include_images=True, include_links=False, include_formatting=True,
            favor_precision=True,
        ) if trafilatura else None
        body_html = extraction_to_html(extracted or "", article["link"])
        body_text = text_content(body_html)
        if len(body_text) < 300:
            body_html = feed_body if len(text_content(feed_body)) >= 300 else f"<p>{html.escape(fallback)}</p>"
            body_text = text_content(body_html)
        article.update({"body_html": body_html, "image": image, "full_text": len(body_text) >= 300})
    except Exception as error:
        print(f"Full-text fetch unavailable for {article['link']}: {error}")
        article.update({"body_html": f"<p>{html.escape(fallback)}</p>", "image": "", "full_text": False})
    return article


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def medium_author_article_body(article_url: str) -> str:
    """Look up a Medium article in its author's full official RSS feed.

    Medium tag feeds intentionally expose short previews, while author feeds
    still carry `content:encoded` with the complete post.
    """
    parsed = urllib.parse.urlsplit(article_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if not parsed.netloc.endswith("medium.com") or not path_parts or not path_parts[0].startswith("@"):
        return ""
    author = path_parts[0]
    if author not in MEDIUM_AUTHOR_FEEDS:
        try:
            root = ET.fromstring(get(f"https://medium.com/feed/{author}"))
            posts = {}
            for node in (entry for entry in root.iter() if entry.tag.rsplit("}", 1)[-1] in {"item", "entry"}):
                link = entry_link(node)
                content = child_text(node, ("encoded", "content"))
                if link and content:
                    posts[canonical_url(link)] = sanitize_feed_html(content)
            MEDIUM_AUTHOR_FEEDS[author] = posts
        except Exception as error:
            print(f"Medium author feed unavailable for {author}: {error}")
            MEDIUM_AUTHOR_FEEDS[author] = {}
    return MEDIUM_AUTHOR_FEEDS[author].get(canonical_url(article_url), "")


def sanitize_feed_html(value: str) -> str:
    """Keep article semantics from a publisher feed while removing active content."""
    if not value:
        return ""
    value = re.sub(r"<(script|style|iframe|object|embed)\b[^>]*>.*?</\1\s*>", "", value, flags=re.I | re.S)
    value = re.sub(r"\s+on[a-z]+\s*=\s*(['\"]).*?\1", "", value, flags=re.I | re.S)
    value = re.sub(r"\s+(href|src)\s*=\s*(['\"])(?:javascript|data):.*?\2", "", value, flags=re.I | re.S)
    # Publisher RSS is HTML, while the formatting/translation pipeline uses an
    # XML parser. Make common HTML-only void tags XML-safe first.
    def close_void_tag(match: re.Match) -> str:
        tag = match.group(0)
        return tag if tag.rstrip().endswith("/>") else tag.rstrip()[:-1] + " />"
    value = re.sub(r"<(?:img|br|hr|source|track|wbr)\b[^>]*>", close_void_tag, value, flags=re.I)
    value = re.sub(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[a-zA-Z][a-zA-Z0-9]+;)", "&amp;", value)
    return value.strip()


def first_image(fragment: str) -> str:
    match = re.search(r"<img\b[^>]*\bsrc\s*=\s*(['\"])(.*?)\1", fragment, flags=re.I | re.S)
    return html.unescape(match.group(2)).strip() if match else ""


def local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def extraction_to_html(extraction: str, base_url: str) -> str:
    """Convert Trafilatura XML to safe reader-friendly HTML without flattening its structure."""
    if not extraction:
        return ""
    try:
        root = ET.fromstring(extraction)
    except ET.ParseError:
        return ""
    main = next((node for node in root.iter() if local_name(node) == "main"), root)
    tags = {"p": "p", "head": "h2", "quote": "blockquote", "list": "ul", "item": "li", "table": "table", "row": "tr", "cell": "td", "code": "pre", "hi": "strong", "lb": "br"}

    def render(node: ET.Element) -> str:
        name = local_name(node)
        if name in {"graphic", "image", "img"}:
            src = node.get("src") or node.get("url") or node.get("href")
            if not src:
                return ""
            src = urllib.parse.urljoin(base_url, src)
            alt = node.get("alt") or node.get("title") or ""
            caption = f"<figcaption>{html.escape(alt)}</figcaption>" if alt else ""
            return f'<figure><img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}" />{caption}</figure>'
        tag = tags.get(name, "div" if name in {"main", "body"} else "p")
        contents = html.escape(node.text or "")
        for child in node:
            contents += render(child) + html.escape(child.tail or "")
        if tag == "br":
            return "<br />"
        return f"<{tag}>{contents}</{tag}>" if contents.strip() or tag in {"ul", "table"} else ""

    rendered = "".join(render(child) + html.escape(child.tail or "") for child in main)
    return rendered or render(main)


def translate_html(fragment: str) -> str:
    """Translate batches of HTML blocks; Google preserves tags and image attributes."""
    try:
        root = ET.fromstring(f"<root>{fragment}</root>")
    except ET.ParseError:
        return paragraphs_from_text(translate(text_content(fragment)))
    blocks = [ET.tostring(child, encoding="unicode", method="html") for child in root]
    batches, current = [], ""
    for block in blocks:
        # Images do not need translation and should not consume API quota.
        if not text_content(block):
            if current:
                batches.append(current)
                current = ""
            batches.append(block)
            continue
        if current and len(current) + len(block) > MAX_TRANSLATION_CHARS:
            batches.append(current)
            current = ""
        current += block
    if current:
        batches.append(current)
    return "".join(translate(batch) if text_content(batch) else batch for batch in batches)


def paragraphs_from_text(value: str) -> str:
    """Last-resort semantic layout when a publisher supplies malformed HTML."""
    blocks = [line.strip() for line in re.split(r"\n{2,}", value) if line.strip()]
    if len(blocks) <= 1:
        blocks = [line.strip() for line in value.splitlines() if line.strip()]
    return "".join(f"<p>{html.escape(block)}</p>" for block in blocks) or f"<p>{html.escape(value)}</p>"


def select_articles(candidates: list[dict]) -> list[dict]:
    candidates.sort(key=score, reverse=True)
    chinese = [item for item in candidates if item["source"].get("language") == "zh" or is_chinese(item["title"])]
    international = [item for item in candidates if item not in chinese]
    selected = chinese[:CHINESE_PER_DAY] + international[:ARTICLES_PER_DAY - CHINESE_PER_DAY]
    if len(selected) < ARTICLES_PER_DAY:
        selected += [item for item in candidates if item not in selected][:ARTICLES_PER_DAY - len(selected)]
    return sorted(selected, key=score, reverse=True)[:ARTICLES_PER_DAY]


def has_usable_body(article: dict) -> bool:
    """Avoid publishing Medium previews when neither official feed offers full text."""
    if not article["source"]["name"].startswith("Medium"):
        return True
    body = medium_author_article_body(article["link"])
    return len(text_content(body)) >= 500


def description_html(article: dict, chinese_title: str, chinese_body_html: str) -> str:
    image = f'<figure><img src="{html.escape(article["image"], quote=True)}" alt="{html.escape(chinese_title, quote=True)}" /></figure>' if article["image"] else ""
    status = "已抓取正文并翻译" if article["full_text"] else "正文抓取受限，以下为来源摘要"
    return (
        f"<p><strong>来源：</strong>{html.escape(article['source']['name'])}　<strong>处理：</strong>{status}</p>"
        f"{image}<h3>中文全文</h3>{chinese_body_html}"
        f"<p><a href=\"{html.escape(article['link'], quote=True)}\">阅读原文 →</a></p>"
    )


def build_item(channel: ET.Element, article: dict) -> dict:
    item = ET.SubElement(channel, "item")
    chinese_title = translate(article["title"])
    chinese_body_html = article["body_html"] if article["source"].get("language") == "zh" else translate_html(article["body_html"])
    content = description_html(article, chinese_title, chinese_body_html)
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
    # A few Medium authors disable full RSS bodies. Exclude their previews and
    # use the next qualified candidate instead of presenting a fake full text.
    rejected = {article["link"] for article in selected if not has_usable_body(article)}
    if rejected:
        selected = [article for article in selected if article["link"] not in rejected]
        selected += [article for article in candidates if article["link"] not in rejected and article not in selected and has_usable_body(article)][:ARTICLES_PER_DAY - len(selected)]
        selected = sorted(selected, key=score, reverse=True)[:ARTICLES_PER_DAY]
    if not selected:
        raise RuntimeError("No new articles found; RSS left unchanged.")
    selected = [fetch_full_article(article) for article in selected]

    ET.register_namespace("content", CONTENT_NS)
    ET.register_namespace("media", MEDIA_NS)
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "全球设计文章精选（中文全文版）"
    ET.SubElement(channel, "link").text = "https://george-li-x.github.io/design-article-rss/"
    ET.SubElement(channel, "description").text = "每日 20 篇设计文章：8 篇中文社区文章、12 篇国际文章的中文全文版，保留正文排版、文内图片和原文链接。"
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
