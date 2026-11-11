# 技术规格：域代码 · CSL-JSON · 本地 API · 校验器

> 按需加载。拼域代码、调 API、写校验器时读本文件。
> 以下为一套在 Zotero 10.x + Word 上验证过的通用技术规格。所有 `<...>` 均为占位符，由使用者的 `config.json` 与本机环境提供；不含任何真实文献、路径、账号或固定编号。

## 1. OOXML 引文域（5-run complex field）

每个元素**各自独立 `w:r`**，不可合并；`instrText` 必须 `xml:space="preserve"`，前后各留一空格。

```xml
<w:r><w:fldChar w:fldCharType="begin"/></w:r>
<w:r><w:instrText xml:space="preserve"> ADDIN ZOTERO_ITEM CSL_CITATION {JSON} </w:instrText></w:r>
<w:r><w:fldChar w:fldCharType="separate"/></w:r>
<w:r><w:t>[n]</w:t></w:r>
<w:r><w:fldChar w:fldCharType="end"/></w:r>
```

> 把 begin / instrText / separate 塞进同一 run 可能导致 Word for Mac 拒绝打开（第三方实践，未官方核验，按 5-run 做最稳）。

## 2. CSL-JSON（`CSL_CITATION` 载荷）

```json
{
  "citationID": "<每处唯一，如 <cid_prefix>-C01-01>",
  "properties": {
    "formattedCitation": "[1]",
    "plainCitation": "[1]",
    "noteIndex": 0
  },
  "citationItems": [{
    "uris": ["http://zotero.org/users/<LIBRARY_ID>/items/<ITEMKEY>"],
    "uri":  ["http://zotero.org/users/<LIBRARY_ID>/items/<ITEMKEY>"],
    "itemData": { "...直接取 format=csljson 原样，不要手工转换..." }
  }],
  "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json"
}
```

硬规矩：
- `plainCitation` 必须与域可见文本 `w:t` **逐字节一致**，否则 Refresh 会提示 citation modified。
- 每个引文域 `citationID` 唯一；同一文献多次引用可复用同一 `itemKey` 与编号，但**不能复用 citationID**。前缀由 `config.cid_prefix` 提供，勿硬编码任何项目缩写。
- `itemData` 直接取 `?format=csljson` 返回的完整对象，**不要**用 API 的 `data.creators` 手工拼装（见 §6 字段 schema 差异）。
- `LIBRARY_ID` 为**本机实际库 id**（个人库常见为 `0`，群组和多人环境可能不同）。用 `GET /api/users/<LIBRARY_ID>/items?limit=1&format=json` 返回的 `library` 对象确认后写入 config；不要凭印象写死。

## 3. 文末书目域（跨段落）

指令：`ADDIN ZOTERO_BIBL {"uncited":[],"omitted":[],"custom":[]} CSL_BIBLIOGRAPHY`

结构（M 条著录 = M 个段落，同一个域包住）：
- 首段：`begin` + `instrText` + `separate` + 第 1 条可见文本
- 中间各段：一条著录文本（无域标记）
- 末段：最后一条著录 + `end`

著录文本由 `?format=json&include=bib,citation&style=<STYLE>` 的单条 `bib` 渲染后取纯文本，去掉 CSL 恒返回的 `[1]` 再按编号表重编号为 `[1]..[M]`。

## 4. 插入点算法（占位符在句号后 → 目标在句号前）

常见实测形态如 `…论述。[REF:<KEY>]`，而目标体例要求 `…论述[1]。`。占位符与句号常分属不同 run，不能简单字符串替换。

```
locate_placeholder(para, key):
  拼出段落全文本 txt，定位 [REF:key] 的 (s, e)
  若 txt[e] == "。"  → 插入点 ins = s（占位符后紧跟句号，删占位符即到位）
  否则向前找最近的 "。" → ins = 该句号下标
rebuild_paragraph(para, s, e, ins, field_runs):
  逐字符重建各 run 文本，保留每个 run 的 rPr
  删除 [REF:key] 子串，在 ins 处插入 5-run 域
  句号与其余正文逐字符不变
```

> 若目标体例的标点不是「。」（例如英文稿用 `.`），把上述判定符号改为 config 中的 `sentence_end` 字符；算法不变。

验证口径：**剔除域可见文本后的正文纯文本 == 剔除占位符后的原文纯文本**（逐段比对，不用整篇一次性比对）。

## 5. 本地 API（Zotero 10+ 官方）

基址 `http://127.0.0.1:23119/api/`，读请求无需认证、离线可用、无速率限制。端口以本机 Zotero 设置为准。

| 用途 | 端点 |
|---|---|
| 条目 CSL-JSON | `GET /api/users/<LIBRARY_ID>/items/<KEY>?format=csljson` |
| 条目数据 + 渲染 | `GET /api/users/<LIBRARY_ID>/items/<KEY>?format=json&include=bib,citation&style=<STYLE>` |

**必须绕过代理**：环境可能有 `HTTP_PROXY` / `HTTPS_PROXY`，本地回环请求会被劫持。

```python
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# 或 curl --noproxy '*'
```

**安全边界**：当前脚本仅发起 GET 请求，并强制 `api_base` 为本机 HTTP 回环地址。它不调用授权端点、不读取 API key、不修改 Zotero 文献库。

**官方无 Word 域写入端点**：Local API 只覆盖 items / collections / searches / tags / fulltext / file uploads。人工 Refresh 是唯一可靠验收路径。

## 6. 字段 schema 差异（易错）

| 来源 | 作者字段 |
|---|---|
| Zotero API `data.creators[]` | `{creatorType, firstName, lastName}` |
| `format=csljson` 的 `author[]` | `{family, given}` |

域内 `itemData` 直接取 csljson 原样。**禁止跨 schema 拼装**。

## 7. 本地数据边界

- 不读取或修改 `zotero.sqlite`、`prefs.js`、`localAPIKeys.json` 等 Zotero 内部文件。
- 不修改自动更新、数据目录、Local API 或其它 Zotero 设置。
- 如果只读 API 返回的文献库与预期不符，停止并由用户在 Zotero 中确认当前文献库。

## 8. 两套校验器（必须隔离）

| | 预填态严格自校 | 成品态只读诊断 |
|---|---|---|
| 时机 | 生成后、Refresh 前 | 用户 Refresh 之后 |
| 检查 | 包部件集合 + SHA-256 仅 `word/document.xml` 变化且无 custom.xml；域计数；域 JSON 全字段；正文逐段零差异；著录 1..M | 域计数、`vertAlign=sup` 数、按 BIBL 域边界取著录段落、残留占位符、URI 匹配 |
| 禁忌 | 不可用于成品态（必然误报） | 著录定位不可写死「最后 N 段」（成品文末可能多空段） |

域 JSON 提取**必须用 XML 元素**：`["".join(it.itertext()) for it in root.iter(Q("instrText"))]`，不要对整段 XML 字符串做正则切分（嵌套花括号会截断 JSON）。

## 9. 样式与渲染

- 样式名由 `config.style` 提供，须为本机已安装样式；**格式选择属决策门 D0，执行前须问用户**（默认建议：中文→GB/T 7714 顺序编码、英文→APA 7，按文章语言）。
- 数字顺序编码样式常含 `<layout vertical-align="sup" delimiter="," prefix="[" suffix="]">` → Refresh 后引文**确定上标**；规划期就实测并写入验收基线，不要事后「修」上标。
- Refresh 后 `formattedCitation` 可能变成 `\super [n]\nosupersub{}`，`plainCitation` 仍为 `[n]`。
- **不打包 CSL 样式文件进 skill**，只用使用者本机已安装样式（许可证不确定）。
- 「合规免责」与「默认采用某样式」是两个层面，不矛盾；若著录常有 PDF 抽取缺陷、且体例文件未标注标准号，删免责会造出「宣称合规却有缺陷」的新矛盾。

## 10. 依赖与解释器

- Python：`lxml`（`etree`）+ 标准库 `zipfile / urllib / hashlib / json / re / html / pathlib`。安装：`pip install lxml`。
- docx 读写底座可用 `python-docx`（MIT），但它**无 Field API**，域必须 `OxmlElement` / lxml 手写。
- 解释器由 `config.python` 指定绝对路径（建议受控 venv，勿用裸 `python` 以免依赖不一致）；**不写死任何本机路径**。
