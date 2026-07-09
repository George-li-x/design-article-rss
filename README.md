# 全球设计文章精选 RSS

每天自动从产品设计、UI、UX、家具、室内与建筑设计领域的权威出版物中筛选 10 篇文章，生成含图文正文的中文 RSS。

## 工作方式

- 以来源权威度、文章时效性、主题匹配度和编辑精选信号综合评分。
- 先抓取正文与文内图片，保留段落、标题、列表和引用；非中文内容会生成中文标题与正文，条目保留原文链接。
- 每日固定目标为 8 篇中文社区文章、12 篇国际文章；Medium 的产品设计、UX 与设计专题也纳入候选。中文候选不足时才以其他合格文章补足。
- 20 篇中以 10 篇 UI/UX 为目标（约 50%），建筑与室内建筑类最多 4 篇，余下名额留给产品、家具、视觉等设计方向。
- `data/seen.json` 记录已发布 URL，避免重复推荐。
- `data/published.json` 只保留 180 天的发布清单；原始 HTML 不入库，完整 RSS 作为 GitHub Pages 的当前部署产物，不会在仓库 Git 历史中长期堆积。
- GitHub Actions 每天执行；可在 Actions 页面手动运行 `Generate design RSS`。
- 生成结果为 `docs/design-rss.xml`，由 GitHub Pages 托管。

## 部署

1. 创建一个 GitHub 仓库并推送本项目。
2. 在仓库 **Settings → Pages** 中选择 **Deploy from a branch**，分支选 `main`，目录选 `/docs`。
3. 在仓库 **Actions** 中手动运行一次 `Generate design RSS`；随后会按计划每日运行。
4. RSS 地址为：`https://<你的-GitHub-用户名>.github.io/<仓库名>/design-rss.xml`

## 说明

翻译优先使用 OpenAI API（密钥只存于 GitHub Actions Secret `OPENAI_API_KEY`），以获得更自然的中文设计术语与更稳定的 HTML 版式保留；API 不可用时才回退到 Google Translate 的公开网页接口。若正文抓取或翻译暂时不可用，条目会明确标注并降级为来源摘要。来源列表与评分规则位于 `config/sources.json` 和 `scripts/generate_rss.py`，可按品味增减。
