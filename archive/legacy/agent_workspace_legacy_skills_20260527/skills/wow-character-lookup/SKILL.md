# wow-character-lookup

- 用途: 当用户要你生成魔兽世界国服角色主页链接、角色档案 URL、角色查询地址时使用
- 触发词: 魔兽, wow, 角色链接, 角色地址, 角色主页, 角色查询, 服务器, 国服角色页, character url

## 规则
- **服务器英文名必须先网上检索最新官方数据**，不得自行编造或依赖旧的映射表
- 服务器英文名必须全小写，首字母不能大写（如 stratholme 而非 Stratholme）
- 优先从用户消息里提取 `服务器名` 和 `角色名`
- 如果只给了角色名，没有服务器名，先追问服务器名，不要硬猜
- 如果给的是中文服务器名，**必须先网上检索**对应英文别名再拼 URL
- **所有非 ASCII 字符都必须做“Unicode 转写”后再输出**。这里的“Unicode 转写”固定指 UTF-8 percent-encoding，即 `%E9%BB%98...` 这种格式
- 最终返回的 URL 必须是纯 ASCII，不能出现任何中文、全角符号或未编码空格
- 角色名必须先编码再放进 path 和 query，不能 path 用中文、只在 query 里编码
- 如果因为兜底逻辑需要直接使用中文服务器名，该服务器名也必须先编码后再拼进 URL
- 如果检索不到服务器对应英文名，可先用原名拼 URL，但需明确告知用户这是兜底拼法
- 返回结果时直接给完整 URL，只发编码后的 ASCII 版本，少解释，不要再附一个中文未编码版本
- 浏览器地址栏有时会把编码后的 URL 显示成中文，这是浏览器展示行为；真正发给用户的文本仍然必须是带 `%` 的编码版

## 步骤
1. 从用户输入中提取服务器名和角色名
2. **网上检索**该服务器的官方英文名（不得自行编造）
3. 把服务器英文名转为全小写，记作 `server_en`
4. 把角色名按 UTF-8 percent-encoding 编码，记作 `encoded_name`
5. 如需兜底使用中文服务器名，先按 UTF-8 percent-encoding 编码成 `encoded_server`
6. 正常情况组装 URL：`https://wow.blizzard.cn/character/#/{server_en}/{encoded_name}?q={encoded_name}`
7. 兜底情况组装 URL：`https://wow.blizzard.cn/character/#/{encoded_server}/{encoded_name}?q={encoded_name}`
8. 输出前自检一次：URL 里如果还含中文，说明失败，必须重写成编码版
9. 直接把 URL 返回给用户

## 示例
- 服务器 `stratholme`，角色名 `冰之川大立屯`：
  `https://wow.blizzard.cn/character/#/stratholme/%E5%86%B0%E4%B9%8B%E5%B7%9D%E5%A4%A7%E7%AB%8B%E5%B1%AF?q=%E5%86%B0%E4%B9%8B%E5%B7%9D%E5%A4%A7%E7%AB%8B%E5%B1%AF`
- 如果临时兜底使用中文服务器名，服务器名也必须先编码，绝不能输出中文路径

## 回复模板
- 信息完整时：直接回完整 URL
- 缺服务器名时：`你把服务器名也发我，我给你直接拼好链接。`
- 服务器检索未命中时：`这个服我没查到官方英文名，我先按你给的拼一个：<URL>，你确认下对不对。`

## 常用服务器映射（参考用，以网上检索结果为准）

### 经典服
- 永恒激励 -> eternal-will
- 光芒照耀 -> shining-light
- 巨龙沼泽 -> dragon-swamp
- 乱舞辉煌 -> glorious-dance
- 米奈希尔 -> menethil
- 哈霍兰 -> hakkar
- 怀特迈恩 -> whitemane
- 奎尔塞拉 -> quelson
- 伦娜 -> lunia

### 正式服
- 死亡之翼 -> deathwing
- 燃烧军团 -> burning-legion
- 风暴之怒 -> stormscale
- 银月 -> silvermoon
- 阿尔萨斯 -> arthas
- 回音山 -> echo-ridge
- 遗忘海岸 -> forgotten-coast
- 神圣之歌 -> holy-song
- 霜之哀伤 -> frostmourne
- 冰霜之棘 -> frostwolf
- 朵丹尼尔 -> dorn-ganymede
- 巴纳扎尔 -> banzaibar
- 狂热之刃 -> bladefist
- 夜幕要塞 -> nightfall
- 冬拥湖 -> wintergrasp
- 阿古斯 -> argus
- 凤凰之魂 -> phoenix-hatchling
- 瓦里安 -> varian
- 白银之手 -> silver-hand
- 血色十字军 -> crusaders-blood
- 诺莫瑞根 -> gnomeregan
- 暗影之月 -> shadowmoon
- 安苏 -> ansu
- 塞纳里奥 -> cenarion-circle
- 碧玉矿洞 -> jadepine
- 埃雷达尔 -> ethredel
- 纳克萨玛斯 -> naxxramas
- 光明使者 -> lightbringer
- 艾欧纳尔 -> aegwynn
- 基尔加丹 -> kiljaeden
- 阿克蒙德 -> archimo
