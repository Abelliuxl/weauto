访问网页时：
1. fetch_url：用于简单HTML/文本，不需要JS渲染的静态页面，如API返回纯文本、无交互的页面。
2. search_web（tavily/brave）：用于搜索信息，查找最新结果，不需要特定页面内容。
3. browse_url（playwright）：用于需要JS渲染的页面，如单页应用、动态加载内容、需要等待的页面。
4. 代理设置：国内网站proxy=false，国际网站（如wowhead）proxy=true。
默认先尝试fetch_url，若返回内容不完整或提示需要JS，则改用browse_url。