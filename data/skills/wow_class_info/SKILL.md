# 魔兽世界职业改动与游戏改动查询策略

## 适用场景

查询魔兽世界职业改动、天赋调整、副本机制、蓝贴公告、PTR测试内容等。

## 查询优先级（按顺序）

### 一级来源（官方/权威报道）

1. **wowhead.com**
   - 直接使用 browse_url，proxy=true（Playwright/Chromium 渲染）
   - 不要先用 fetch_url 抓 wowhead：首页和新闻页正文抓取容易被 CloudFront 返回 403
   - 首页/分类页通常有 Latest News / PTR 板块
   - 搜索建议：site:wowhead.com + 职业名 + 版本号 + 改动/nerf/buff
   - wowhead 会汇总蓝贴内容并附分析和数据挖掘

2. **暴雪美服官方论坛 - 蓝贴追踪器**
   - URL: https://us.forums.blizzard.com/en/wow/g/blizzard-tracker/activity/posts
   - 使用 browse_url，proxy=true
   - 这是改动最核心的来源，所有正式发布的蓝贴（hotfix、补丁说明、职业调整）都在这里

### 二级来源（当官方来源没有明确信息时）

如果 wowhead 和暴雪蓝贴追踪器都找不到相关改动，说明该改动可能只是 PTR 上的测试状态，并非官方发布的正式改动。此时可以查阅：

3. **NGA 论坛**
   - URL: https://nga.178.com/ 或 https://bbs.nga.cn/
   - 搜索：site:bbs.nga.cn + 职业名 + 改动/PTR
   - proxy=false（国内站）
   - 注意：NGA 内容多为玩家主观判断和测试感受

4. **Reddit（r/wow、r/CompetitiveWoW）**
   - 搜索：site:reddit.com/r/wow + 职业名 + changes/PTR
   - proxy=true

5. **美服官方论坛玩家讨论区**
   - https://us.forums.blizzard.com/en/wow/c/classes/
   - proxy=true

## 注意事项

- 一级来源（wowhead + 蓝贴追踪器）没找到的内容，大概率不是官方正式改动，只是 PTR 测试的临时状态
- 二级来源的内容是玩家主观判断，不是官方蓝贴改动，回复时必须注明"这是玩家测试反馈/论坛讨论，并非官方蓝贴"
- 如果检索了以上所有来源仍无结果，如实告知用户"目前没有找到相关官方改动信息"
- 查询 wowhead 时直接用 browse_url；如果 browse_url 失败，再改用 search_web/search_web_brave 搜索 `site:wowhead.com` 的具体文章
- 查询暴雪官方新闻页/论坛蓝贴时优先 browse_url；若 fetch_url 已经明确成功，可使用 fetch_url 的结果
