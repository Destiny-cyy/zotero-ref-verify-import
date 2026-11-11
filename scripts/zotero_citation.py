# -*- coding: utf-8 -*-
"""Zotero 引用「核实 + 导入」· 参数化核心脚本（通用版）

只读优先：inspect / verify / diagnose / material 只读，不写任何文件（核实主线）。
写操作只有 build，且必须显式 --apply；默认 dry-run 只打印计划（导入主线）。
所有路径、映射表、编号表、样式、库 id、cid 前缀、句末标点均外置到 config.json，
不硬编码任何项目专属值、真实文献数据或本机路径。

前置：pip install lxml；Zotero 桌面端运行中；本机 Local API 已启用。
解释器由 config.python 指定绝对路径（建议受控 venv，勿用裸 python）。

子命令：
  inspect    只读基线：API 连通、样式渲染实测、docx 包结构、占位符定位、映射核对
  verify     预填态严格静态自校（生成后、Refresh 前）
  diagnose   成品态只读结构诊断（Refresh 之后用，不误报）
  material   dump 交付素材：著录全文 + 编号表 + 存疑候选段落
  build      生成预填态活文档（--probe 仅 1 个引文域；--apply 才真正写）
"""
import sys, os, re, json, copy, html, zipfile, hashlib, urllib.parse, urllib.request, pathlib, argparse

try:
    from lxml import etree
except ImportError:
    sys.exit("缺少 lxml：请先 pip install lxml")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XMLSPACE = "{http://www.w3.org/XML/1998/namespace}space"
NS = {"w": W}
def Q(tag):
    return "{%s}%s" % (W, tag)

SCHEMA = "https://github.com/citation-style-language/schema/raw/master/csl-citation.json"
BIBL_CFG = {"uncited": [], "omitted": [], "custom": []}
UA = "zotero-ref-verify-import/1.0"
# 关键：本地回环请求必须绕过 HTTP_PROXY / HTTPS_PROXY
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------- config
REQUIRED = ("source_docx", "deliver_docx", "api_base", "style", "citation_plan", "key_to_itemkey")

def load_config(path):
    cfg = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        sys.exit("config 缺少必填字段: %s" % ", ".join(missing))
    cfg.setdefault("user_id", 0)
    cfg.setdefault("cid_prefix", "CITE")
    cfg.setdefault("sentence_end", "。")
    cfg.setdefault("verify_copy_docx", "")
    cfg.setdefault("doubt_paras", [])
    api_url = urllib.parse.urlparse(cfg["api_base"])
    if api_url.scheme != "http" or api_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        sys.exit("api_base 仅允许本机 HTTP 回环地址（127.0.0.1 / localhost / ::1）")
    cfg["_paths"] = {k: pathlib.Path(v) for k, v in cfg.items()
                     if (k.endswith("_docx") and v) or (k == "data_dir" and v)}
    return cfg


# ---------------------------------------------------------------- API（只读）
def api_get(cfg, path, timeout=25):
    req = urllib.request.Request(cfg["api_base"].rstrip("/") + path,
                                 headers={"User-Agent": UA})
    with opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def fetch_csl(cfg, itemkey):
    uid = cfg["user_id"]
    d = json.loads(api_get(cfg, "/api/users/%s/items/%s?format=csljson" % (uid, itemkey)))
    return (d or [{}])[0]

def fetch_render(cfg, itemkey):
    uid = cfg["user_id"]
    query = urllib.parse.urlencode({
        "format": "json",
        "include": "bib,citation",
        "style": cfg["style"],
    })
    d = json.loads(api_get(cfg, "/api/users/%s/items/%s?%s" % (uid, itemkey, query)))
    return d.get("citation"), d.get("bib")

def bib_plain(bib_html):
    txt = html.unescape(re.sub(r"<[^>]+>", "", bib_html or ""))
    txt = re.sub(r"\[(\d+)\](?=\S)", r"[\1] ", txt)  # 编号与正文间补回空格
    return re.sub(r"[ \t]+", " ", txt).strip()


# ---------------------------------------------------------------- OOXML 域
def add_text_run(p, text):
    r = etree.SubElement(p, Q("r"))
    t = etree.SubElement(r, Q("t"))
    t.set(XMLSPACE, "preserve")
    t.text = text

def make_field(instr, visible):
    """返回 5 个独立 w:r：begin / instrText / separate / w:t(可见) / end"""
    runs = []
    r = etree.Element(Q("r")); fc = etree.SubElement(r, Q("fldChar")); fc.set(Q("fldCharType"), "begin"); runs.append(r)
    r = etree.Element(Q("r")); it = etree.SubElement(r, Q("instrText")); it.set(XMLSPACE, "preserve"); it.text = " " + instr + " "; runs.append(r)
    r = etree.Element(Q("r")); fc = etree.SubElement(r, Q("fldChar")); fc.set(Q("fldCharType"), "separate"); runs.append(r)
    r = etree.Element(Q("r")); t = etree.SubElement(r, Q("t")); t.set(XMLSPACE, "preserve"); t.text = visible; runs.append(r)
    r = etree.Element(Q("r")); fc = etree.SubElement(r, Q("fldChar")); fc.set(Q("fldCharType"), "end"); runs.append(r)
    return runs

def citation_json(num, itemkey, csl_item, cid, uid=0):
    uri = "http://zotero.org/users/%s/items/%s" % (uid, itemkey)
    visible = "[%d]" % num
    return {
        "citationID": cid,
        "properties": {"formattedCitation": visible, "plainCitation": visible, "noteIndex": 0},
        "citationItems": [{"uris": [uri], "uri": [uri], "itemData": csl_item}],
        "schema": SCHEMA,
    }

def citation_instr(js):
    return "ADDIN ZOTERO_ITEM CSL_CITATION " + json.dumps(js, separators=(",", ":"), ensure_ascii=False)

def bibl_instr():
    return "ADDIN ZOTERO_BIBL " + json.dumps(BIBL_CFG, separators=(",", ":")) + " CSL_BIBLIOGRAPHY"


# ---------------------------------------------------------------- 段落工具
def para_runs(p):
    return p.findall(Q("r"))

def para_text(p):
    return "".join(t.text or "" for t in p.iter(Q("t")))

def locate_placeholder(p, key, sentence_end="。"):
    """返回 (txt, s, e, ins)：占位符范围与句末标点前的插入点。"""
    runs = para_runs(p)
    segs, pos = [], 0
    for r in runs:
        for t in r.findall(Q("t")):
            s = t.text or ""
            segs.append([pos, pos + len(s), s, r, t])
            pos += len(s)
    txt = "".join(x[2] for x in segs)
    m = re.search(r"\[REF:%s\]" % re.escape(key), txt)
    if not m:
        return None
    s, e = m.span()
    if e < len(txt) and txt[e] == sentence_end:
        ins = s                       # 占位符后紧跟句末标点，删掉即到位
    else:
        k = txt.rfind(sentence_end, 0, s)
        ins = k if k >= 0 else s      # 插到前面最近的句末标点之前
    return txt, s, e, ins

def rebuild_paragraph(p, s, e, ins, field_runs):
    """删除占位符，并在精确字符边界插入域；不重排段落的其它文本。"""
    runs = para_runs(p)
    segs, pos = [], 0
    for r in runs:
        for t in r.findall(Q("t")):
            v = t.text or ""
            segs.append([pos, pos + len(v), v, r, t])
            pos += len(v)

    if not (0 <= ins <= s < e <= pos):
        raise ValueError("占位符或插入点范围无效")

    # 只删除与占位符重叠的文本，已有域和其它 run 保持原位。
    for a, b, value, _run, text_node in segs:
        left = max(s, a)
        right = min(e, b)
        if left < right:
            text_node.text = value[:left - a] + value[right - a:]

    # ins 不晚于 s，因此删除占位符不会改变插入点之前的字符偏移。
    for a, b, v, r, t in segs:
        if a <= ins <= b:
            offset = ins - a
            current = t.text or ""
            if offset == 0:
                idx = list(p).index(r)
                for i, field_run in enumerate(field_runs):
                    p.insert(idx + i, field_run)
                return
            if offset == len(current):
                idx = list(p).index(r) + 1
                for i, field_run in enumerate(field_runs):
                    p.insert(idx + i, field_run)
                return

            text_nodes = r.findall(Q("t"))
            allowed = {Q("rPr"), Q("t")}
            if len(text_nodes) != 1 or any(child.tag not in allowed for child in r):
                raise ValueError("插入点位于复杂 run 内，无法无损拆分；请先在 Word 中拆分该 run")

            t.text = current[:offset]
            tail_run = etree.Element(Q("r"))
            rpr = r.find(Q("rPr"))
            if rpr is not None:
                tail_run.append(copy.deepcopy(rpr))
            tail_text = etree.SubElement(tail_run, Q("t"))
            tail_text.set(XMLSPACE, "preserve")
            tail_text.text = current[offset:]
            idx = list(p).index(r) + 1
            for i, field_run in enumerate(field_runs):
                p.insert(idx + i, field_run)
            p.insert(idx + len(field_runs), tail_run)
            return

    if ins == pos and runs:
        idx = list(p).index(runs[-1]) + 1
        for i, field_run in enumerate(field_runs):
            p.insert(idx + i, field_run)
        return

    raise ValueError("无法定位引用域插入点")

def append_bibliography_field(root, entries):
    """跨段落 BIBL 域：begin/instr/separate 在首段，end 在末段，中间每条一段。"""
    if not entries:
        raise ValueError("参考文献列表为空，不能创建书目域")
    body = root.find(Q("body"))
    sectPr = body.find(Q("sectPr"))
    idx = list(body).index(sectPr) if sectPr is not None else len(list(body))
    paras = []
    p0 = etree.Element(Q("p"))
    r = etree.SubElement(p0, Q("r")); fc = etree.SubElement(r, Q("fldChar")); fc.set(Q("fldCharType"), "begin")
    r = etree.SubElement(p0, Q("r")); it = etree.SubElement(r, Q("instrText")); it.set(XMLSPACE, "preserve"); it.text = " " + bibl_instr() + " "
    r = etree.SubElement(p0, Q("r")); fc = etree.SubElement(r, Q("fldChar")); fc.set(Q("fldCharType"), "separate")
    add_text_run(p0, entries[0])
    if len(entries) == 1:
        r = etree.SubElement(p0, Q("r")); fc = etree.SubElement(r, Q("fldChar")); fc.set(Q("fldCharType"), "end")
        paras.append(p0)
    else:
        paras.append(p0)
    for e in entries[1:-1]:
        p = etree.Element(Q("p")); add_text_run(p, e); paras.append(p)
    if len(entries) > 1:
        plast = etree.Element(Q("p")); add_text_run(plast, entries[-1])
        r = etree.SubElement(plast, Q("r")); fc = etree.SubElement(r, Q("fldChar")); fc.set(Q("fldCharType"), "end")
        paras.append(plast)
    for off, p in enumerate(paras):
        body.insert(idx + off, p)
    return len(paras)


# ---------------------------------------------------------------- 编号表
def num_to_key(cfg):
    out = {}
    for row in cfg["citation_plan"]:
        n = row["num"]
        if n not in out:
            out[n] = (row["key"], row["itemkey"])
    return out


def validated_output_paths(cfg):
    src = cfg["_paths"]["source_docx"]
    outputs = [cfg["_paths"]["deliver_docx"]]
    if cfg.get("verify_copy_docx"):
        outputs.append(cfg["_paths"]["verify_copy_docx"])
    source_resolved = os.path.normcase(str(src.resolve()))
    output_resolved = [os.path.normcase(str(out.resolve())) for out in outputs]
    if source_resolved in output_resolved:
        raise ValueError("输出路径不得与 source_docx 原稿相同")
    if len(set(output_resolved)) != len(output_resolved):
        raise ValueError("deliver_docx 与 verify_copy_docx 不得指向同一文件")
    return outputs


# ---------------------------------------------------------------- 子命令（核实主线，只读）
def cmd_inspect(cfg):
    print("=" * 72); print("inspect · 只读基线（不写任何文件）"); print("=" * 72)
    src = cfg["_paths"]["source_docx"]
    if not src.exists():
        sys.exit("原稿不存在: %s" % src)
    try:
        d = json.loads(api_get(cfg, "/api/users/%s/items?limit=1&format=json" % cfg["user_id"]))
        print("本地 API 连通: True | 返回条目数(样本):", len(d))
    except Exception as ex:
        sys.exit("本地 API 不通（检查 Zotero 是否运行 / Local API 是否启用 / 代理绕过）: %r" % ex)
    ek = next(iter(cfg["key_to_itemkey"].values()))
    cit, bib = fetch_render(cfg, ek)
    print("样式渲染实测: style=%s" % cfg["style"])
    print("  citation:", repr(cit))
    print("  bib 纯文本:", repr(bib_plain(bib))[:160])
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        root = etree.fromstring(z.read("word/document.xml"))
    paras = root.find(Q("body")).findall(Q("p"))
    print("包部件数:", len(names), "| 含 docProps/custom.xml:", "docProps/custom.xml" in names)
    print("body 直属段落数:", len(paras))
    found = []
    for i, p in enumerate(paras, 1):
        for k in re.findall(r"\[REF:([A-Za-z0-9\-]+)\]", para_text(p)):
            found.append((i, k))
    print("占位符数:", len(found), "| 计划引用点数:", len(cfg["citation_plan"]))
    plan = [(r["para"], r["key"]) for r in cfg["citation_plan"]]
    print("定位结果与编号表一致:", found == plan)
    if found != plan:
        print("  !! 实定位:", found)
        print("  !! 计划表:", plan)
    missing = [k for _, k in found if k not in cfg["key_to_itemkey"]]
    print("未映射的 REF 键:", missing or "无")
    print(">>> inspect 完成（只读）")


def instr_texts(root):
    return ["".join(it.itertext()) for it in root.iter(Q("instrText"))]


def narrative_paragraph_texts(root, limit=None):
    """取正文纯文本，忽略 complex field 的可见结果。"""
    results = []
    depth = 0
    paragraphs = root.find(Q("body")).findall(Q("p"))
    for p in paragraphs[:limit]:
        parts = []
        for run in p.findall(Q("r")):
            for child in run:
                if child.tag == Q("fldChar"):
                    kind = child.get(Q("fldCharType"))
                    if kind == "begin":
                        depth += 1
                    elif kind == "end":
                        depth = max(0, depth - 1)
                elif child.tag == Q("t") and depth == 0:
                    parts.append(child.text or "")
        results.append("".join(parts))
    return results


def bibliography_entries(root):
    """按 ZOTERO_BIBL 域边界提取书目段落，不误收正文中的 [n]。"""
    entries = []
    active = False
    separated = False
    for p in root.find(Q("body")).findall(Q("p")):
        parts = []
        for run in p.findall(Q("r")):
            instruction = "".join(run.itertext()) if run.find(Q("instrText")) is not None else ""
            if "ZOTERO_BIBL" in instruction:
                active = True
            field_char = run.find(Q("fldChar"))
            if field_char is not None:
                kind = field_char.get(Q("fldCharType"))
                if active and kind == "separate":
                    separated = True
                elif active and kind == "end":
                    active = False
                    separated = False
            elif active and separated:
                parts.extend(t.text or "" for t in run.findall(Q("t")))
        text = "".join(parts)
        if text:
            entries.append(text)
    return entries

def cmd_verify(cfg, path):
    """预填态严格静态自校。不可用于已 Refresh 的成品（必然误报）。"""
    print("=" * 72); print("verify · 预填态严格静态自校"); print("=" * 72)
    src, tgt = cfg["_paths"]["source_docx"], pathlib.Path(path)
    with zipfile.ZipFile(src) as z:
        oi = z.infolist(); ob = {i.filename: z.read(i.filename) for i in oi}
    with zipfile.ZipFile(tgt) as z:
        pi = z.infolist(); pb = {i.filename: z.read(i.filename) for i in pi}
    ok = True
    same = [i.filename for i in oi] == [i.filename for i in pi]
    diff = [n for n in ob if hashlib.sha256(ob[n]).hexdigest() != hashlib.sha256(pb[n]).hexdigest()]
    print("包部件集合一致:", same, "| 含 custom.xml:", "docProps/custom.xml" in pb, "| 变化部件:", diff)
    if not same or diff != ["word/document.xml"] or "docProps/custom.xml" in pb:
        ok = False
    oroot = etree.fromstring(ob["word/document.xml"])
    proot = etree.fromstring(pb["word/document.xml"])
    cit = [t for t in instr_texts(proot) if "CSL_CITATION" in t]
    bibl = [t for t in instr_texts(proot) if "ZOTERO_BIBL" in t]
    field_counts = {
        kind: len(proot.xpath(".//w:fldChar[@w:fldCharType=$kind]", namespaces=NS, kind=kind))
        for kind in ("begin", "separate", "end")
    }
    print("CSL_CITATION 域:", len(cit), "| ZOTERO_BIBL 域:", len(bibl),
          "| begin/separate/end:", field_counts["begin"], field_counts["separate"], field_counts["end"])
    expect = 1 if cfg.get("_probe") else len(cfg["citation_plan"])
    expected_fields = expect + 1
    if len(cit) != expect or len(bibl) != 1 or any(v != expected_fields for v in field_counts.values()):
        ok = False
    ids, nums = set(), []
    for idx, t in enumerate(cit, 1):
        js = json.loads(t.strip().split("CSL_CITATION", 1)[1].strip())
        ids.add(js["citationID"])
        nums.append(int(re.sub(r"[^0-9]", "", js["properties"]["plainCitation"])))
        row = cfg["citation_plan"][idx - 1]
        want_key = row["itemkey"]
        want_visible = "[%d]" % row["num"]
        want_uri = "http://zotero.org/users/%s/items/%s" % (cfg["user_id"], want_key)
        item = js.get("citationItems", [{}])[0]
        properties = js.get("properties", {})
        if (js.get("schema") != SCHEMA or item.get("uris") != [want_uri]
                or item.get("uri") != [want_uri] or not item.get("itemData", {}).get("type")
                or properties.get("plainCitation") != want_visible
                or properties.get("formattedCitation") != want_visible):
            print("  !! 第%d域 schema/URI/itemData/可见编号异常" % idx); ok = False
    print("citationID 唯一:", len(ids), "/", len(cit), "| 编号序列:", nums)
    if len(ids) != len(cit):
        ok = False
    residual = len(re.findall(r"\[REF:", "".join(para_text(p) for p in proot.find(Q("body")).findall(Q("p")))))
    print("残留 [REF:] 占位符:", residual)
    if residual:
        ok = False
    source_paras = oroot.find(Q("body")).findall(Q("p"))
    expected_texts = [re.sub(r"\[REF:[A-Za-z0-9\-]+\]", "", para_text(p)) for p in source_paras]
    actual_texts = narrative_paragraph_texts(proot, len(source_paras))
    narrative_ok = expected_texts == actual_texts
    print("正文逐段纯文本零差异:", narrative_ok)
    if not narrative_ok:
        ok = False
    bib_texts = bibliography_entries(proot)
    bib_nums = [int(m.group(1)) for text in bib_texts if (m := re.match(r"^\[(\d+)\]", text))]
    expected_nums = sorted(num_to_key(cfg))
    bibliography_ok = len(bib_texts) == len(expected_nums) and bib_nums == expected_nums
    print("书目条数/编号连续去重:", bibliography_ok, "| 编号序列:", bib_nums)
    if not bibliography_ok:
        ok = False
    print(">>> verify 总结论:", "通过" if ok else "不通过（硬停止）")
    return 0 if ok else 2

def cmd_diagnose(cfg, path):
    """成品态只读结构诊断（Refresh 之后用，不用预填态口径，避免误报）。"""
    print("=" * 72); print("diagnose · 成品态只读结构诊断"); print("=" * 72)
    with zipfile.ZipFile(pathlib.Path(path)) as z:
        xml = z.read("word/document.xml")
        has_custom = "docProps/custom.xml" in z.namelist()
    root = etree.fromstring(xml)
    s = xml.decode("utf-8", "replace")
    print("含 docProps/custom.xml:", has_custom, "（Refresh 后由 Zotero 插件写入，非违规）")
    cit = [t for t in instr_texts(root) if "CSL_CITATION" in t]
    bibl = [t for t in instr_texts(root) if "ZOTERO_BIBL" in t]
    print("CSL_CITATION 域:", len(cit), "| ZOTERO_BIBL 域:", len(bibl), "| vertAlign(sup):", s.count("vertAlign"))
    texts = [para_text(p) for p in root.find(Q("body")).findall(Q("p"))]
    bib_entries = bibliography_entries(root)
    nums = [int(re.match(r"\[(\d+)\]", t).group(1)) for t in bib_entries]
    print("著录段落数:", len(bib_entries), "| 编号序列:", nums)
    print("残留 [REF:] 占位符:", len(re.findall(r"\[REF:", "".join(texts))))
    print(">>> diagnose 完成（只读；不判通过/不通过，逐项人工核对）")

def cmd_material(cfg):
    print("=" * 72); print("material · 交付素材 dump（只读）"); print("=" * 72)
    print("\n### 著录全文")
    for n, (key, itemkey) in sorted(num_to_key(cfg).items()):
        _c, bib = fetch_render(cfg, itemkey)
        print("[%d] %s" % (n, re.sub(r"^\[\d+\]\s*", "", bib_plain(bib))))
    print("\n### 编号表")
    for r in cfg["citation_plan"]:
        print("段%03d | %s | 顺序%02d | 编号[%d] | %s | %s" %
              (r["para"], r.get("cid", ""), r["seq"], r["num"], r["key"], r["itemkey"]))
    print("\n### 存疑候选段落（无占位符）")
    with zipfile.ZipFile(cfg["_paths"]["source_docx"]) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    paras = root.find(Q("body")).findall(Q("p"))
    for i in cfg.get("doubt_paras", []):
        if 1 <= i <= len(paras):
            print("\n-- 段%03d --\n%s" % (i, para_text(paras[i - 1])))


# ---------------------------------------------------------------- 子命令（导入主线，写）
def cmd_build(cfg, probe=False, apply=False):
    print("=" * 72); print("build · %s%s" % ("最小探针" if probe else "全量注入", "（--apply 写入）" if apply else "（dry-run）")); print("=" * 72)
    src = cfg["_paths"]["source_docx"]
    try:
        outs = validated_output_paths(cfg)
    except ValueError as ex:
        sys.exit("%s，硬停止" % ex)
    with zipfile.ZipFile(src) as z:
        infos = z.infolist()
        blobs = {i.filename: z.read(i.filename) for i in infos}
    root = etree.fromstring(blobs["word/document.xml"])
    paras = root.find(Q("body")).findall(Q("p"))
    plan = cfg["citation_plan"][:1] if probe else cfg["citation_plan"]
    csl_cache = {}
    def csl_for(k):
        if k not in csl_cache:
            csl_cache[k] = fetch_csl(cfg, k)
        return csl_cache[k]
    se = cfg["sentence_end"]
    prefix = cfg["cid_prefix"]
    for row in plan:
        p, seq, num, key, itemkey = row["para"], row["seq"], row["num"], row["key"], row["itemkey"]
        cid = row.get("cid", "C%02d" % seq)
        loc = locate_placeholder(paras[p - 1], key, se)
        if loc is None:
            sys.exit("段%03d 未找到 [REF:%s]，硬停止" % (p, key))
        txt, s, e, ins, = loc
        js = citation_json(num, itemkey, csl_for(itemkey), "%s-%s-%02d" % (prefix, cid, seq), cfg["user_id"])
        print("  段%03d %s 编号[%d] %s -> %s | cid=%s" % (p, cid, num, key, itemkey, js["citationID"]))
        if apply:
            rebuild_paragraph(paras[p - 1], s, e, ins, make_field(citation_instr(js), "[%d]" % num))
    if not apply:
        print("  dry-run：未写任何文件。确认计划后加 --apply")
        return
    entries = []
    for n, (key, itemkey) in sorted(num_to_key(cfg).items()):
        _c, bib = fetch_render(cfg, itemkey)
        body_txt = re.sub(r"^\[\d+\]\s*", "", bib_plain(bib))
        entries.append("[%d] %s" % (n, body_txt))
        print("  [%d] %s" % (n, entries[-1][:110]))
    append_bibliography_field(root, entries)
    blobs["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    for out in outs:
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w") as zout:
            for i in infos:
                zout.writestr(i, blobs[i.filename])
        print("  已生成:", out)


def main():
    ap = argparse.ArgumentParser(description="Zotero 引用核实 + 导入 · 参数化核心（默认只读）")
    ap.add_argument("--config", default=str(pathlib.Path(__file__).resolve().parents[1] / "assets" / "config.example.json"))
    ap.add_argument("--file", help="verify/diagnose 的目标 docx")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inspect")
    p = sub.add_parser("build"); p.add_argument("--probe", action="store_true"); p.add_argument("--apply", action="store_true")
    sub.add_parser("verify"); sub.add_parser("diagnose"); sub.add_parser("material")
    a = ap.parse_args()
    cfg = load_config(a.config)
    if a.cmd == "inspect":   cmd_inspect(cfg)
    elif a.cmd == "build":
        cfg["_probe"] = a.probe
        cmd_build(cfg, a.probe, a.apply)
    elif a.cmd == "verify":
        f = a.file or str(cfg["_paths"]["deliver_docx"])
        sys.exit(cmd_verify(cfg, f))
    elif a.cmd == "diagnose":
        f = a.file or str(cfg["_paths"]["deliver_docx"])
        cmd_diagnose(cfg, f)
    elif a.cmd == "material": cmd_material(cfg)

if __name__ == "__main__":
    main()
