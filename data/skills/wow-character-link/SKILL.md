---
name: wow-character-link
description: Build Chinese WoW official armory character links from character/server, character name in the local table, or player+class lookup. Use when asked for 魔兽/WoW/暴雪国服角色链接, 角色页面, 玩家某职业角色, or to update the local role table.
---

# WoW Character Link

- 触发词: 魔兽角色链接, WoW角色主页, 角色页面, 角色主页, 主页, 玩家职业角色, build_wow_character_url

Use this skill to resolve a World of Warcraft CN character and build its official character URL.

## URL Rule

Template:

```text
https://wow.blizzard.cn/character/#/{realm_slug}/{encoded_character}?q={encoded_character}
```

Encode the exact character name with URL percent-encoding using UTF-8. Example:

```text
屯屯魅影 + 斯坦索姆 -> stratholme
encoded = %E5%B1%AF%E5%B1%AF%E9%AD%85%E5%BD%B1
https://wow.blizzard.cn/character/#/stratholme/%E5%B1%AF%E5%B1%AF%E9%AD%85%E5%BD%B1?q=%E5%B1%AF%E5%B1%AF%E9%AD%85%E5%BD%B1
```

Do not use `run_python` to URL-encode names. This skill has a modular tool action:

```json
{"type":"build_wow_character_url","player":"吴松竹","class_name":"战士","title":"群-临沧"}
```

The action is implemented by `data/skills/wow-character-link/builder.py`, which owns TSV lookup and UTF-8 percent-encoding.

## Resolution Order

1. If the user gives `角色名 + 服务器`, use the server table to get `realm_slug`, encode the character name, and build the URL.
2. If the user gives only a character name, look it up in the character table by `character` or `aliases`.
3. If the user asks for `玩家 + 职业`, find rows where `player` or `aliases` matches the player phrase and `class` matches.
4. If exactly one row matches, call `build_wow_character_url`; do not call `run_python`.
5. If multiple rows match, list candidates and ask for the missing discriminator.
6. If the server exists but the `status` is `draft`, still build the URL, but say the server slug is from the draft table and may need correction.
7. If no server slug exists, do not invent silently. Ask for the server slug or add a row to the server table if the user provides it.

## Maintenance

The bundled tables are also stored as:

- `data/skills/wow-character-link/characters.tsv`
- `data/skills/wow-character-link/servers.tsv`
- `data/skills/wow-character-link/builder.py`
- `data/skills/wow-character-link/tool.json`

When only `write_skill` is available, update the embedded tables below in this `SKILL.md`. When file-edit tools are available, keep the TSV files and the embedded tables in sync.

Add a character row when the user explicitly gives a player/character/server/class mapping. Add player or character nicknames in `aliases` separated by `|`.

## Server Table

| server | realm_slug | aliases | status |
|---|---|---|---|
| 斯坦索姆 | stratholme |  | verified |
| 伊森利恩 | isillien |  | verified |
| 丽丽（四川） | li-li | 丽丽\|丽丽四川 | verified |
| 回音山 | echo-ridge |  | verified |
| 凤凰之神 | alar | 凤凰\|凤凰之神 | verified |
| 霜之哀伤 | frostmourne |  | verified |
| 通灵学院 | scholomance |  | draft |
| 死亡之翼 | deathwing |  | verified |
| 影之哀伤 | shadowmourne |  | verified |
| 米奈希尔 | menethil |  | verified |
| 图拉扬 | turalyon |  | verified |
| 诺兹多姆 | nozdormu |  | verified |
| 神圣之歌 | holy-chanter |  | verified |
| 冰风岗 | chillwind-point |  | verified |
| 伊利丹 | illidan |  | verified |
| 银色之手 | silver-hand |  | verified |
| 金色平原 | the-golden-plains |  | verified |

## Character Table

| player | character | server | class | aliases |
|---|---|---|---|---|
| 周思驭 | 小芮瑾年 | 伊森利恩 | 猎人 | ZZ\|zz |
| 高锟 | 画戟小枝 | 丽丽（四川） | 唤魔师 | 丨神 |
| 刘晓亮 | 嘭地一声 | 回音山 | 德鲁伊 | 亮仔 |
| 刘晓亮 | 邪能肖战 | 回音山 | 恶魔猎手 | 亮仔 |
| 刘晓亮 | 淸真之刃 | 回音山 | 战士 | 亮仔 |
| 刘晓亮 | 羅志祥 | 回音山 | 萨满 | 亮仔 |
| 刘晓亮 | 体育生 | 回音山 | 武僧 | 亮仔 |
| 字众明 | 辣子鸡不要鸡 | 凤凰之神 | 德鲁伊 | 亮神 |
| 张鑫鹏 | 阿瘫 | 霜之哀伤 | 萨满 | 元神 |
| 张鑫鹏 | 柯志华 | 霜之哀伤 | 恶魔猎手 | 元神 |
| 张鑫鹏 | 帮帮鼠鼠 | 霜之哀伤 | 唤魔师 | 元神 |
| 吴松竹 | 体育老师 | 通灵学院 | 战士 | 吴工 |
| 吴松竹 | 邀月 | 丽丽（四川） | 圣骑士 | 吴工 |
| 吴松竹 | 黑魔仙豹哥 | 死亡之翼 | 死亡骑士 | 吴工 |
| 屯狗 | 屯屯宝宝 | 斯坦索姆 | 猎人 |  |
| 屯狗 | 屯屯魅影 | 斯坦索姆 | 术士 |  |
| 巨奶 | 傻瓜观测 | 影之哀伤 | 牧师 |  |
| 巨奶 | 毛顺屎圆 | 米奈希尔 | 圣骑士 |  |
| 巨奶 | 小鲤鱼啵啵 | 影之哀伤 | 德鲁伊 |  |
| 张捷 | 四个自信 | 回音山 | 法师 | 捷教授 |
| 张捷 | 低保仔 | 回音山 | 术士 | 捷教授 |
| 蒋昶 | 冲锋先看路 | 霜之哀伤 | 战士 | 昶狂 |
| 蒋昶 | 战复慢点起 | 霜之哀伤 | 死亡骑士 | 昶狂 |
| 段瑜 | 生锈的斩牛刀 | 伊森利恩 | 盗贼 | 段总 |
| 段瑜 | 飞翔的潼瑜 | 伊森利恩 | 死亡骑士 | 段总 |
| 李洋 | 洋锅 | 凤凰之神 | 圣骑士 | 洋锅 |
| 统皇 | 焦糖扁可颂 | 斯坦索姆 | 圣骑士 |  |
| 统皇 | 本间芽衣芓 | 斯坦索姆 | 战士 |  |
| 统皇 | 生命众筹 | 斯坦索姆 | 死亡骑士 |  |
| 统皇 | 亻沈默 | 图拉扬 | 法师 |  |
| 昭言 | Fountine | 图拉扬 | 法师 | 舒总\|绍言 |
| 昭言 | 天灵浴血 | 诺兹多姆 | 死亡骑士 | 舒总\|绍言 |
| 昭言 | 霜满天丶 | 图拉扬 | 恶魔猎手 | 舒总\|绍言 |
| 昭言 | 长夜咏叹调 | 图拉扬 | 牧师 | 舒总\|绍言 |
| 昭言 | Fotinedragon | 图拉扬 | 唤魔师 | 舒总\|绍言 |
| 小蔡 | 莱恩弗尔特 | 神圣之歌 | 猎人 | 蔡圣 |
| 小蔡 | 亚妮艾丝 | 冰风岗 | 牧师 | 蔡圣 |
| 小蔡 | 亚里欧斯 | 神圣之歌 | 恶魔猎手 | 蔡圣 |
| 小蔡 | 伊格瑞特 | 神圣之歌 | 德鲁伊 | 蔡圣 |
| 小蔡 | 萨里西翁 | 神圣之歌 | 萨满 | 蔡圣 |
| 小蔡 | 罗赛莉亚 | 神圣之歌 | 术士 | 蔡圣 |
| 龙神 | Emotional | 伊利丹 | 牧师 |  |
