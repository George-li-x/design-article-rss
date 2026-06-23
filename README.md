# 全球设计文章精选 RSS

每天自动从产品设计、UI、UX、家具、室内与建筑设计领域的权威出版物中筛选 10 篇文章，生成中文 RSS。

## 工作方式

- 以来源权威度、文章时效性、主题匹配度和编辑精选信号综合评分。
- 非中文内容会生成中文标题和中文摘要，条目会保留原文链接。
- `data/seen.json` 记录已发布 URL，避免重复推荐。
- GitHub Actions 每天执行；可在 Actions 页面手动运行 `Generate design RSS`。
- 生成结果为 `docs/design-rss.xml`，由 GitHub Pages 托管。

## 部署

1. 创建一个 GitHub 仓库并推送本项目。
2. 在仓库 **Settings → Pages** 中选择 **Deploy from a branch**，分支选 `main`，目录选 `/docs`。
3. 在仓库 **Actions** 中手动运行一次 `Generate design RSS`；随后会按计划每日运行。
4. RSS 地址为：`https://<你的-GitHub-用户名>.github.io/<仓库名>/design-rss.xml`

## 说明

翻译使用 Google Translate 的公开网页接口，不需要密钥；若单篇翻译暂时不可用，仍会保留原始标题与摘要。来源列表与评分规则位于 `config/sources.json` 和 `scripts/generate_rss.py`，可按品味增减。
