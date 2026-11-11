# 踩坑清单与误报识别

> 按需加载。报错、结果反常、校验器不通过时读本文件。
> 通用纪律：出现 Bug 先写「复现测试」确认源头，再最小修复；同一结论只验证一次。
> 本文件举例均为通用占位（`N` / `M` / `<KEY>` / `<ITEMKEY>`），不含任何真实文献或项目数据。

格式：**现象 → 根因 → 修法 → 下次预防**

## P1 本地 API 连不上 / 请求被劫持
- 现象：`http://127.0.0.1:23119` 失败或返回非预期内容
- 根因：环境变量 `HTTP_PROXY` / `HTTPS_PROXY`，回环请求走了代理
- 修法：模块级 `urllib.request.build_opener(ProxyHandler({}))`；curl 用 `--noproxy '*'`
- 预防：所有本地 API 脚本统一在模块级构造无代理 opener，不用默认 opener

## P2 样式名含空格或非 ASCII 字符
- 现象：渲染请求报 URL 格式错误，或 Zotero 没有收到完整样式名
- 根因：把样式名直接拼到查询字符串，没有 URL 编码
- 修法：使用 `urllib.parse.urlencode` 构造查询参数
- 预防：所有 Local API 查询参数统一编码，不手拼 URL

## P3 `api_base` 指向非本机地址
- 现象：配置加载时硬停止
- 根因：当前版本只允许本机 Zotero Local API，避免向远程地址发送文献元数据
- 修法：使用 `http://127.0.0.1:23119`、`http://localhost:23119` 或本机 IPv6 回环地址
- 预防：不要把 `api_base` 配成局域网或公网服务

## P4 输出路径与原稿相同
- 现象：`build` 在读稿前硬停止
- 根因：`deliver_docx` 或 `verify_copy_docx` 指向 `source_docx`
- 修法：为交付件和验证副本分别设置新文件名
- 预防：原稿始终只读；脚本会拒绝相同路径和重复输出路径

## P5 CSL numeric 样式 Refresh 后强制上标
- 现象：行内 `[n]` 变上标，`formattedCitation` 变 `\super [n]\nosupersub{}`，预填态校验器全判异常
- 根因：样式 `vertical-align="sup"`，**非文档损坏**
- 修法：预填态仍写行内 `[n]` 保证交付瞬间可见；把上标列为已接受差异；终稿以用户刷新后的上标态为准
- 预防：规划期实测样式 citation 渲染，写入验收基线，不事后「修」上标

## P6 Word/Zotero Refresh 自行写入 custom.xml → 预填态自校误报（**最常见**）
- 现象：交付前重跑 `verify` 报「不通过」：包部件集合不一致、多个部件 SHA 变化、含 `custom.xml`；各引文域全判异常；可见文本抽到 `N+1` 个 `[n]`；著录编号报缺 `[1]`
- 根因（多因叠加）：
  1. Word 另存重写多个部件 + Zotero 插件写入 `ZOTERO_PREF_1`（样式 id / hasBibliography=1，活文档记住样式所必需，**非我方违规注入**）
  2. 上标格式串（见 P5）
  3. 正则多抓书目域首条 `[1]`
  4. 成品文末多空段，使「最后 M 段」窗口整体后移，把第 1 条挤出
- 修法：判定为**校验器口径不匹配**，改用只读结构诊断逐项复核（域计数 / sup 数 / 按 BIBL 域边界取著录段落 / 占位符 / URI）
- 预防：**预填态自校与成品态诊断两套隔离，不交叉重跑**；著录定位不写死「最后 M 段」

## P7 占位符原在句号后，需重写 run
- 现象：`…论述。[REF:<KEY>]` 而目标体例要求 `…论述[1]。`
- 根因：占位符与句号 / 标点分属不同 run，不能简单字符串替换
- 修法：`locate_placeholder` 判定插入点（占位符后紧跟句末标点 → 放原位；否则插到前面最近的句末标点前），`rebuild_paragraph` 逐字符重建 run、保留各 run 的 `rPr`；英文稿把判定符号改为 `.`（见 spec.md §4）
- 预防：定位阶段先输出「插入点 + run 结构」自检；验证用「剔除域可见文本 vs 剔除占位符」逐段比对

## P8 PDF 抽取缺陷：断字 / `&amp;` 双重转义 / title 处理
- 现象：期刊名/标题出现 `&amp;` 双重转义、单词内丢字母、断词连写、单词粘连、整串连写等抽取缺陷
- 根因：PDF / HTML 抽取丢字母与双重转义；若项目体例要求「title 逐字保留原文」，则禁止规范化标题覆盖
- 修法：**按 C5 / C7 设定处理**——默认原样保留、逐条加注（编号 / 原样 / 疑似正确形式 / 成因 / 处理）；仅在明确允许时规范化。`bib_plain` 只做 `html.unescape` 与 `[n]` 后补空格的最小文本化
- 预防：把「缺陷登记 + 加注」做成交付物固定章节；匹配文献用 DOI / 模糊标题，勿做精确标题匹配

## P9 creators 字段跨 schema 拼装
- 现象：按 `family` / `given` 读取普通 API 的 creators 取不到值
- 根因：API `data.creators[]` 是 `{creatorType, firstName, lastName}`，`format=csljson` 的 `author[]` 才是 `{family, given}`
- 修法：域内 `itemData` **直接取 csljson 原样**
- 预防：域构造只消费 csljson，不把普通 API creators 手工转换后混入

## P10 探针静态自校 3 个实现细节
- `[n]` 后缺空格 → 补 `re.sub(r"\[(\d+)\](?=\S)", r"[\1] ", txt)`
- 整篇一次性纯文本比对掩盖段落差异 → 改**逐段**比对，允许文末书目段落为增量
- 从 instrText 字符串切 JSON 被嵌套花括号截断 → **用 XML 元素 `itertext()` 取 instrText 再 `json.loads`**
- 预防：域 JSON 一律走 XML 元素解析，不做字符串切割

## P11 Local API 返回的库与预期不符
- 现象：目标 itemKey 查不到，或 `inspect` 返回的样本明显不是预期文献库
- 根因：Zotero 当前打开了另一套数据目录或库 id 配置错误
- 修法：停止运行，由用户在 Zotero 中确认当前文献库和 `user_id`
- 预防：先运行 `inspect`，确认样本和 library 信息后再生成文档

## P12 著录首条编号后的空格差异
- 现象：导出列表 `[1] AUTHOR…`，Word 内 CSL 悬挂缩进渲染为 `[1]AUTHOR…`
- 根因：编号与正文间由样式缩进控制，无硬空格
- 修法：列为已知渲染差异加注，不改内容
- 预防：导出列表时统一补空格并在交付说明登记

## 反例警示（不得采用）
- 直接读写 `zotero.sqlite` 并 `taskkill /F zotero.exe`（第三方项目做法，有数据损坏风险）
- 复制 AGPLv3 源码或无许可证仓库代码
- 用「预填态校验器」复核已 Refresh 的成品并据此「修复」
- 验证失败后 Unlink Citations 或退化静态定稿
- 读取或记录 Zotero API key、账号凭据或内部配置文件

## 误报识别速查
| 报错 | 先别改文件，先查 |
|---|---|
| 包部件 / SHA 大面积变化 + 含 custom.xml | 文件是否刚被 Word 打开另存 / Refresh 过 → P6 |
| 域全判「异常」但 cid / 编号 / URI 都对 | `formattedCitation` 是否变 `\super ...` → P5 |
| 可见 `[n]` 多 1 个 | 正则是否多抓书目首条 → P6 |
| 著录缺第 1 条 | 是否写死「最后 M 段」且文末多空段 → P6 |
| 样式名请求失败 / API 地址被拒 / 输出路径被拒 | P2 / P3 / P4 |
