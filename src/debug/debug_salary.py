"""
debug_salary.py — BOSS直聘薪资元素提取与混淆解析测试

BOSS直聘使用 kanzhun-mix 自定义字体将薪资数字替换为私用区 Unicode（U+E031–U+E03A），
视觉上显示为数字，但 innerText 读到乱码。本脚本探索解码路径。

解码思路（按优先级）：
  A. 字体文件解析：从 CSS @font-face 获取字体 URL → 下载 → fonttools 解析 cmap → 得到映射表
  B. Canvas 像素比对：将私用区字符用 kanzhun-mix 渲染到 canvas，与标准数字像素比对
  C. 人工映射：直接输出原始码点，供人工或调试工具确认

运行前提：
  - start_chrome_job.bat 已启动（port 9222）
  - 已导航到 BOSS直聘职位列表页（页面上有岗位卡片）

用法：
  python src/debug/debug_salary.py
"""
import sys
import json
import struct
import base64
import io
import requests
import pychrome
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CDP_SCANNER_URL

CDP_URL = CDP_SCANNER_URL

OK   = "[OK  ]"
MISS = "[MISS]"
INFO = "[INFO]"
WARN = "[WARN]"


def sep(title="", width=64):
    print()
    print("=" * width)
    if title:
        print(f"  {title}")
        print("=" * width)


def eval_js(tab, js: str, label: str = ""):
    try:
        raw = tab.call_method("Runtime.evaluate", expression=js,
                              returnByValue=True, timeout=15)
        return raw.get("result", {}).get("value")
    except Exception as e:
        print(f"  {WARN} {label} JS错误: {e}")
        return None


# ── GROUP 1：原始薪资元素提取 ─────────────────────────────────────────────────

def check_salary_elements(tab) -> list[dict]:
    sep("GROUP 1 — 原始薪资元素提取（.job-salary）")

    js = """
    (function() {
        const els = Array.from(document.querySelectorAll('.job-salary'));
        return JSON.stringify(els.map((el, i) => {
            const txt  = el.innerText || '';
            const codes = Array.from(txt).map(c => c.codePointAt(0));
            const cls   = (el.className || '').trim();
            const style = window.getComputedStyle(el).fontFamily || '';
            return { idx: i, raw: txt, codes, cls, fontFamily: style };
        }));
    })()
    """
    raw = eval_js(tab, js, "薪资元素")
    if not raw:
        print(f"  {MISS} 未获取到薪资元素（页面上没有岗位卡片？）")
        return []

    items = json.loads(raw)
    if not items:
        print(f"  {MISS} .job-salary 数量为 0")
        return []

    print(f"  {OK} 找到 {len(items)} 个 .job-salary 元素\n")

    # 统计私用区字符
    all_private: dict[int, int] = {}   # codepoint → 出现次数

    for it in items[:10]:   # 只显示前10个
        raw_txt = it["raw"]
        codes   = it["codes"]
        private = [c for c in codes if 0xE000 <= c <= 0xF8FF]

        display_codes = " ".join(
            f"U+{c:04X}{'*' if 0xE000 <= c <= 0xF8FF else ''}"
            for c in codes
        )
        print(f"  [{it['idx']:02d}] raw={raw_txt!r:<18}  codes: {display_codes}")

        for cp in private:
            all_private[cp] = all_private.get(cp, 0) + 1

    if len(items) > 10:
        print(f"  ... 共 {len(items)} 个，只显示前10")

    print(f"\n  {INFO} 私用区码点（* 标记）说明：kanzhun-mix 字体将这些码点渲染为数字字形")
    print(f"  {INFO} 发现的私用区码点（共 {len(all_private)} 种）：")
    for cp in sorted(all_private):
        print(f"         U+{cp:04X}  出现 {all_private[cp]} 次")

    # 检查字体名
    font_families = set(it["fontFamily"] for it in items if it["fontFamily"])
    print(f"\n  {INFO} 薪资元素 font-family: {font_families}")

    return items


# ── GROUP 2：CSS @font-face 字体文件 URL ────────────────────────────────────

def find_font_url(tab) -> str:
    sep("GROUP 2 — CSS @font-face 字体 URL 提取")
    import re

    font_url = ""

    # ── 方法 A：遍历所有 styleSheet（用 rule.type===5 替代 constructor.name）──
    js_sheets = """
    (function() {
        const results = [];
        for (const sheet of document.styleSheets) {
            try {
                for (const rule of sheet.cssRules) {
                    if (rule.type !== 5) continue;   // 5 = CSSRule.FONT_FACE_RULE
                    const family = rule.style.getPropertyValue('font-family') || '';
                    const src    = rule.style.getPropertyValue('src') || '';
                    if (family.toLowerCase().includes('kanzhun') ||
                        src.toLowerCase().includes('kanzhun')) {
                        results.push({ family, src, href: sheet.href || '(inline)' });
                    }
                }
            } catch(e) { /* cross-origin 跳过 */ }
        }
        return JSON.stringify(results);
    })()
    """
    raw = eval_js(tab, js_sheets, "styleSheet font-face")
    if raw:
        rules = json.loads(raw)
        if rules:
            print(f"  {OK} styleSheets 中找到 {len(rules)} 条 kanzhun @font-face：")
            for r in rules:
                print(f"    family={r['family']!r}  sheet={r['href']}")
                print(f"    src={r['src'][:200]}")
                urls = re.findall(r'url\(["\']?(https?://[^"\')\s]+)["\']?\)', r["src"])
                if not urls:
                    urls = re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', r["src"])
                if urls:
                    font_url = font_url or urls[0]
                    print(f"    {OK} URL: {urls[0]}")
        else:
            print(f"  {WARN} styleSheets 中未找到 kanzhun @font-face（可能跨域或动态注入）")

    # ── 方法 B：扫描 <style> 标签原始文本（JS 动态注入的字体走这里）─────────
    if not font_url:
        js_style_tags = """
        (function() {
            const hits = [];
            document.querySelectorAll('style').forEach(el => {
                const text = el.textContent || '';
                if (!text.includes('kanzhun')) return;
                // 提取包含 kanzhun 的 @font-face 块
                const matches = text.match(/@font-face\\s*\\{[^}]*kanzhun[^}]*\\}/g) || [];
                matches.forEach(m => hits.push(m.slice(0, 500)));
            });
            return JSON.stringify(hits);
        })()
        """
        raw2 = eval_js(tab, js_style_tags, "<style> 标签扫描")
        if raw2:
            hits = json.loads(raw2)
            if hits:
                print(f"\n  {OK} <style> 标签中找到 {len(hits)} 条 kanzhun @font-face：")
                for h in hits:
                    print(f"    {h[:300]}")
                    urls = re.findall(r'url\(["\']?(https?://[^"\')\s]+)["\']?\)', h)
                    if not urls:
                        urls = re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', h)
                    if urls:
                        font_url = font_url or urls[0]
                        print(f"    {OK} URL: {urls[0]}")
            else:
                print(f"  {WARN} <style> 标签中也未找到 kanzhun")

    # ── 方法 C：performance entries（字体请求记录）────────────────────────────
    if not font_url:
        js_perf = """
        (function() {
            return JSON.stringify(
                performance.getEntriesByType('resource')
                    .filter(e => /kanzhun/.test(e.name) ||
                                 (/woff|ttf|otf/.test(e.name) && e.initiatorType === 'css'))
                    .map(e => ({ name: e.name, type: e.initiatorType }))
            );
        })()
        """
        raw3 = eval_js(tab, js_perf, "performance entries")
        if raw3:
            entries = json.loads(raw3)
            if entries:
                print(f"\n  {OK} performance entries 中找到字体请求：")
                for e in entries:
                    print(f"    {e['name']}")
                    if re.search(r'kanzhun', e['name']):
                        font_url = font_url or e['name']
            else:
                print(f"  {WARN} performance entries 中未找到字体请求")

    # ── 方法 D：在页面 HTML / 脚本中暴力搜索 kanzhun 字体 URL ───────────────
    if not font_url:
        js_search = """
        (function() {
            const html = document.documentElement.outerHTML;
            const m = html.match(/https?:[^'"\\s)]+kanzhun[^'"\\s)]*\\.(?:woff2?|ttf|otf)/g);
            return JSON.stringify(m || []);
        })()
        """
        raw4 = eval_js(tab, js_search, "全文搜索")
        if raw4:
            urls = json.loads(raw4)
            if urls:
                print(f"\n  {OK} 页面 HTML 中找到字体 URL：")
                for u in urls:
                    print(f"    {u}")
                font_url = font_url or urls[0]
            else:
                print(f"  {WARN} 页面 HTML 中也未找到 kanzhun 字体 URL")

    if font_url:
        print(f"\n  {OK} 最终使用字体 URL: {font_url}")
    else:
        print(f"\n  {MISS} 无法获取字体 URL — kanzhun-mix 可能以 blob: 或 base64 形式嵌入")

    return font_url


# ── GROUP 3：字体文件下载与解析 ──────────────────────────────────────────────

def parse_font_file(font_url: str) -> dict[int, int]:
    """
    下载字体文件并解析 cmap，返回 {私用区码点: 数字(0-9)} 映射。
    优先用 fonttools；不可用则尝试手动解析 woff/ttf cmap。
    """
    sep("GROUP 3 — 字体文件下载与 cmap 解析")

    if not font_url:
        print(f"  {WARN} 未获取到字体 URL，跳过本组")
        return {}

    print(f"  下载: {font_url}")
    try:
        resp = requests.get(font_url, timeout=10,
                            headers={"Referer": "https://www.zhipin.com/"})
        resp.raise_for_status()
        font_data = resp.content
        print(f"  {OK} 下载成功，大小 {len(font_data):,} 字节，"
              f"Content-Type={resp.headers.get('Content-Type','')}")
    except Exception as e:
        print(f"  {MISS} 下载失败: {e}")
        return {}

    # 方案A：fonttools
    try:
        from fontTools.ttLib import TTFont
        print(f"\n  {OK} fonttools 可用，解析中...")
        return _parse_with_fonttools(font_data)
    except ImportError:
        print(f"  {INFO} fonttools 未安装（pip install fonttools），尝试手动解析")

    # 方案B：手动解析 woff → sfnt → cmap
    return _parse_woff_manual(font_data)


def _parse_with_fonttools(font_data: bytes) -> dict[int, int]:
    from fontTools.ttLib import TTFont
    import io

    font = TTFont(io.BytesIO(font_data))
    mapping: dict[int, int] = {}

    # 获取所有 cmap 子表
    cmap_table = font.getBestCmap()
    if not cmap_table:
        print(f"  {MISS} 未找到 cmap 表")
        return {}

    print(f"  cmap 共 {len(cmap_table)} 个码点映射")

    # 找私用区码点
    private_entries = {cp: gid for cp, gid in cmap_table.items()
                       if 0xE000 <= cp <= 0xF8FF}

    if not private_entries:
        print(f"  {WARN} cmap 中无私用区（U+E000–U+F8FF）条目")
        return {}

    print(f"  {OK} 私用区映射（共 {len(private_entries)} 个）：")

    # 获取 glyph 名称 → 推断数字
    glyph_names = font.getGlyphNames()
    DIGIT_WORDS = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    }
    glyph_order = font.getGlyphOrder()

    for cp in sorted(private_entries):
        gid  = private_entries[cp]
        name = glyph_order[gid] if gid < len(glyph_order) else f"glyph{gid}"

        # 从 glyph 名猜数字（zero/one/.../nine 或 uni0030-uni0039）
        digit = None
        name_lower = name.lower()
        for word, d in DIGIT_WORDS.items():
            if word in name_lower:
                digit = d
                break
        if digit is None:
            # uni0030 = '0' ... uni0039 = '9'
            import re
            m = re.search(r'uni0*3([0-9])', name, re.IGNORECASE)
            if m:
                digit = int(m.group(1))

        if digit is not None:
            mapping[cp] = digit
            print(f"    U+{cp:04X} → glyph '{name}' → {digit}")
        else:
            print(f"    U+{cp:04X} → glyph '{name}' → (未识别)")

    return mapping


def _parse_woff_manual(font_data: bytes) -> dict[int, int]:
    """
    手动从 woff/woff2/ttf 字节中定位 cmap 表，提取私用区→标准 ASCII 映射。
    仅覆盖 cmap format 4（最常见）。
    """
    # 检测格式
    magic = font_data[:4]
    if magic == b'wOFF':
        print(f"  {INFO} 格式: WOFF，解包 sfnt...")
        sfnt = _woff_to_sfnt(font_data)
        if not sfnt:
            print(f"  {MISS} WOFF 解包失败")
            return {}
    elif magic == b'wOF2':
        print(f"  {MISS} WOFF2 需要 Brotli 解压（pip install brotli 或 fonttools）")
        return {}
    elif magic in (b'\x00\x01\x00\x00', b'true', b'OTTO'):
        print(f"  {INFO} 格式: TTF/OTF，直接解析")
        sfnt = font_data
    else:
        print(f"  {MISS} 未知格式: {magic!r}")
        return {}

    return _parse_sfnt_cmap(sfnt)


def _woff_to_sfnt(data: bytes) -> bytes | None:
    """将 WOFF 转换为 sfnt 字节（简化版，仅处理未压缩表）。"""
    try:
        # WOFF header
        (flavor, length, num_tables, _, sfnt_size,
         major, minor, meta_offset, meta_length, meta_orig_length,
         priv_offset, priv_length) = struct.unpack_from(">IHHHIHHIIII", data, 4)

        tables_info = []
        offset = 44
        for _ in range(num_tables):
            tag, o, comp_len, orig_len, checksum = struct.unpack_from(">4sIIII", data, offset)
            tables_info.append((tag, o, comp_len, orig_len))
            offset += 20

        # 重建 sfnt（仅支持未压缩表）
        import zlib
        out_tables = {}
        for tag, o, comp_len, orig_len in tables_info:
            raw = data[o: o + comp_len]
            if comp_len < orig_len:
                try:
                    raw = zlib.decompress(raw)
                except Exception:
                    return None
            out_tables[tag] = raw

        # 简单返回 cmap 表字节（跳过完整 sfnt 重建）
        if b'cmap' in out_tables:
            return out_tables[b'cmap']   # 返回 cmap 原始字节
        return None
    except Exception as e:
        print(f"  {WARN} WOFF 解包异常: {e}")
        return None


def _parse_sfnt_cmap(data: bytes) -> dict[int, int]:
    """
    从 sfnt 字节流解析 cmap 表（format 4），返回私用区→标准字符的映射。
    如果 data 本身就是 cmap 表字节（由 _woff_to_sfnt 返回），直接解析。
    否则先定位 cmap 表偏移量。
    """
    mapping: dict[int, int] = {}
    try:
        # 判断是完整 sfnt 还是单独的 cmap 表
        if data[:4] in (b'\x00\x01\x00\x00', b'true', b'OTTO'):
            # 完整 sfnt：找 cmap 表偏移
            num_tables = struct.unpack_from(">H", data, 4)[0]
            cmap_offset = None
            for i in range(num_tables):
                tag = data[12 + i * 16: 12 + i * 16 + 4]
                if tag == b'cmap':
                    cmap_offset = struct.unpack_from(">I", data, 12 + i * 16 + 8)[0]
                    break
            if cmap_offset is None:
                print(f"  {MISS} sfnt 中未找到 cmap 表")
                return {}
            cmap_data = data[cmap_offset:]
        else:
            # 直接是 cmap 表字节
            cmap_data = data

        version, num_subtables = struct.unpack_from(">HH", cmap_data, 0)
        print(f"  {INFO} cmap version={version}  subtables={num_subtables}")

        for i in range(num_subtables):
            platform, encoding, sub_offset = struct.unpack_from(
                ">HHI", cmap_data, 4 + i * 8
            )
            fmt = struct.unpack_from(">H", cmap_data, sub_offset)[0]
            print(f"    subtable[{i}] platform={platform} encoding={encoding} format={fmt}")

            if fmt != 4:
                continue

            # cmap format 4
            length, language, seg_count_x2 = struct.unpack_from(
                ">HHH", cmap_data, sub_offset + 2
            )
            seg_count = seg_count_x2 // 2
            segs_base = sub_offset + 14

            end_codes   = [struct.unpack_from(">H", cmap_data, segs_base + j*2)[0]
                           for j in range(seg_count)]
            start_codes = [struct.unpack_from(">H", cmap_data, segs_base + seg_count*2 + 2 + j*2)[0]
                           for j in range(seg_count)]
            id_deltas   = [struct.unpack_from(">h", cmap_data, segs_base + seg_count*4 + 2 + j*2)[0]
                           for j in range(seg_count)]
            id_range_offsets_base = segs_base + seg_count*6 + 2
            id_range_offsets = [struct.unpack_from(">H", cmap_data, id_range_offsets_base + j*2)[0]
                                for j in range(seg_count)]

            for j in range(seg_count):
                sc, ec, delta, roff = (start_codes[j], end_codes[j],
                                       id_deltas[j], id_range_offsets[j])
                if sc == 0xFFFF:
                    continue
                # 只关心私用区
                if ec < 0xE000 or sc > 0xF8FF:
                    continue
                for cp in range(max(sc, 0xE000), min(ec, 0xF8FF) + 1):
                    if roff == 0:
                        gid = (cp + delta) & 0xFFFF
                    else:
                        idx = id_range_offsets_base + j*2 + roff + (cp - sc)*2
                        gid = struct.unpack_from(">H", cmap_data, idx)[0]
                        if gid != 0:
                            gid = (gid + delta) & 0xFFFF
                    # GID 0x30-0x39 = '0'-'9'
                    if 0x30 <= gid <= 0x39:
                        mapping[cp] = gid - 0x30
                    elif gid != 0:
                        mapping[cp] = gid   # 未识别，存原始 GID

        if mapping:
            print(f"\n  {OK} 私用区→数字映射（cmap format 4）：")
            for cp in sorted(mapping):
                print(f"    U+{cp:04X} → {mapping[cp]}")
        else:
            print(f"  {WARN} format 4 中未找到私用区→标准数字映射")
    except Exception as e:
        print(f"  {MISS} cmap 解析异常: {e}")
        import traceback; traceback.print_exc()

    return mapping


# ── GROUP 4：Canvas 像素比对（备用方案）────────────────────────────────────

def canvas_digit_recognition(tab, salary_items: list[dict]) -> dict[int, int]:
    sep("GROUP 4 — Canvas 像素比对（备用/验证）")

    # 收集所有私用区码点
    private_chars = set()
    for it in salary_items:
        for cp in it["codes"]:
            if 0xE000 <= cp <= 0xF8FF:
                private_chars.add(cp)

    if not private_chars:
        print(f"  {INFO} 未发现私用区字符，跳过")
        return {}

    print(f"  {INFO} 待识别私用区字符: {sorted(f'U+{cp:04X}' for cp in private_chars)}")

    # 先获取薪资元素的 font-family
    js_font = """
    (function() {
        const el = document.querySelector('.job-salary');
        return el ? window.getComputedStyle(el).fontFamily : '"kanzhun-mix",sans-serif';
    })()
    """
    kanzhun_font = eval_js(tab, js_font, "kanzhun font-family") or '"kanzhun-mix",sans-serif'
    print(f"  {INFO} 使用字体: {kanzhun_font}")

    # 单字符渲染函数（每次调用只渲染一个字符，避免超时）
    _JS_RENDER_ONE = """
    (function(ch, fontStr) {
        const SIZE = 20;
        const c = document.createElement('canvas');
        c.width = SIZE; c.height = SIZE;
        const ctx = c.getContext('2d');
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, SIZE, SIZE);
        ctx.fillStyle = '#000';
        ctx.font = '16px ' + fontStr;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(ch, SIZE/2, SIZE/2);
        const data = ctx.getImageData(0, 0, SIZE, SIZE).data;
        // 只取红色通道的反转值（白底黑字 → 字形像素亮）
        let s = '';
        for (let i = 0; i < data.length; i += 4)
            s += String.fromCharCode(255 - data[i]);
        return btoa(s);
    })(%s, %s)
    """

    def render_char(ch: str) -> list[int] | None:
        js = _JS_RENDER_ONE % (json.dumps(ch), json.dumps(kanzhun_font))
        try:
            raw = tab.call_method("Runtime.evaluate", expression=js,
                                  returnByValue=True, timeout=8)
            b64 = raw.get("result", {}).get("value")
            if b64:
                return list(base64.b64decode(b64))
        except Exception as e:
            print(f"  {WARN} 渲染 {ch!r} 失败: {e}")
        return None

    # 渲染参考数字 0-9
    print(f"  渲染参考数字 0-9...")
    ref_pixels: list[list[int] | None] = []
    for d in "0123456789":
        ref_pixels.append(render_char(d))

    # 渲染待识别私用区字符
    print(f"  渲染私用区字符...")
    test_chars = sorted(private_chars)
    test_pixels: list[list[int] | None] = []
    for cp in test_chars:
        test_pixels.append(render_char(chr(cp)))

    def mse(a: list[int], b: list[int]) -> float:
        return sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)

    def dark_pixel_count(pixels: list[int]) -> int:
        return sum(1 for p in pixels if p > 30)

    mapping: dict[int, int] = {}

    print(f"\n  {'字符':<8}  {'码点':<10}  {'最佳匹配'}  {'MSE':>8}  {'置信度'}")
    print(f"  {'─'*8}  {'─'*10}  {'─'*8}  {'─'*8}  {'─'*8}")

    for i, cp in enumerate(test_chars):
        ch = chr(cp)
        tp = test_pixels[i]

        if tp is None:
            print(f"  {ch!r:<8}  U+{cp:04X}    (渲染失败)")
            continue

        test_dark = dark_pixel_count(tp)
        if test_dark < 5:
            print(f"  {ch!r:<8}  U+{cp:04X}    (空白，字体未渲染此字符)")
            continue

        valid_refs = [(j, ref_pixels[j]) for j in range(10) if ref_pixels[j] is not None]
        if not valid_refs:
            print(f"  {ch!r:<8}  U+{cp:04X}    (参考数字渲染均失败)")
            continue

        scores = sorted((mse(tp, rp), j) for j, rp in valid_refs)
        best_mse, best_digit = scores[0]
        second_mse = scores[1][0] if len(scores) > 1 else best_mse
        confidence = "高" if second_mse > best_mse * 2 else ("中" if second_mse > best_mse * 1.3 else "低")

        mapping[cp] = best_digit
        print(f"  {ch!r:<8}  U+{cp:04X}    {best_digit}         {best_mse:>8.1f}  {confidence}")

    return mapping


# ── GROUP 5：应用映射，展示解码结果 ─────────────────────────────────────────

def decode_salaries(salary_items: list[dict], mapping: dict[int, int]):
    sep("GROUP 5 — 解码薪资结果")

    if not mapping:
        print(f"  {WARN} 未获得映射表，无法解码")
        print(f"  {INFO} 原始薪资文字（含乱码）：")
        for it in salary_items[:10]:
            print(f"    [{it['idx']:02d}] {it['raw']!r}")
        return

    print(f"  使用映射表: { {f'U+{k:04X}': v for k, v in sorted(mapping.items())} }\n")

    def decode(codes: list[int]) -> str:
        result = []
        for cp in codes:
            if cp in mapping:
                result.append(str(mapping[cp]))
            else:
                result.append(chr(cp))
        return "".join(result)

    all_ok = 0
    print(f"  {'idx':<5}  {'原始（乱码）':<20}  {'解码结果'}")
    print(f"  {'─'*5}  {'─'*20}  {'─'*20}")
    for it in salary_items:
        decoded = decode(it["codes"])
        # 检查是否有未被识别的私用区字符残留（非数字、非已知标点）
        residual_private = [c for c in decoded
                            if 0xE000 <= ord(c) <= 0xF8FF]
        if not residual_private:
            all_ok += 1
        flag = f"  ← 未映射私用区: {[f'U+{ord(c):04X}' for c in residual_private]}" if residual_private else ""
        print(f"  {it['idx']:<5}  {it['raw']!r:<20}  {decoded!r}{flag}")

    print(f"\n  {OK if all_ok==len(salary_items) else WARN} "
          f"完全解码: {all_ok}/{len(salary_items)}")


# ── GROUP 6：手动码点对照表（人工验证辅助）─────────────────────────────────

def print_codepoint_table(salary_items: list[dict]):
    sep("GROUP 6 — 私用区码点完整列表（供人工/DevTools 验证）")

    all_private: dict[int, set] = {}  # cp → 出现在哪些薪资文本中
    for it in salary_items:
        for cp in it["codes"]:
            if 0xE000 <= cp <= 0xF8FF:
                all_private.setdefault(cp, set()).add(it["raw"])

    if not all_private:
        print(f"  {INFO} 未发现私用区字符")
        return

    print(f"  共 {len(all_private)} 个不同私用区码点：\n")
    print(f"  {'码点':<10}  {'chr()':<6}  {'出现次数'}  示例薪资")
    print(f"  {'─'*10}  {'─'*6}  {'─'*8}  {'─'*20}")
    for cp in sorted(all_private):
        examples = list(all_private[cp])[:2]
        print(f"  U+{cp:04X}    {chr(cp)!r:<6}  {len(all_private[cp]):<8}  "
              + " | ".join(repr(e) for e in examples))

    print(f"""
  {INFO} DevTools 验证方法：
       1. F12 → Console 执行：
          document.querySelector('.job-salary').innerText.split('').map(c => c.codePointAt(0).toString(16))
       2. 对比上表，确认码点一致。
       3. 在 Sources → 字体文件（kanzhun-*.woff2）中下载字体，
          用 https://fontdrop.info 查看私用区字形。
  """)


# ── 连接 CDP ──────────────────────────────────────────────────────────────────

def connect():
    try:
        tabs_info = requests.get(f"{CDP_URL}/json", timeout=5).json()
    except Exception as e:
        print(f"[失败] 无法连接 {CDP_URL}: {e}"); sys.exit(1)

    boss = next(
        (t for t in tabs_info
         if "zhipin.com" in t.get("url", "") and t.get("type") == "page"), None
    )
    if not boss:
        print("[失败] 未找到 BOSS直聘 标签页"); sys.exit(1)

    print(f"[标签页] {boss.get('title','')[:60]}")
    print(f"[URL]    {boss.get('url','')}")

    browser = pychrome.Browser(url=CDP_URL)
    tab = next((t for t in browser.list_tab() if t.id == boss["id"]), None)
    if not tab:
        print("[失败] pychrome 找不到标签页"); sys.exit(1)

    tab.start()
    print("[CDP] 连接成功")
    return tab


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("  debug_salary.py — 薪资混淆解析测试")
    print("=" * 64)

    tab = connect()
    try:
        salary_items = check_salary_elements(tab)
        if not salary_items:
            print("\n[退出] 无薪资元素可供测试")
            return

        font_url = find_font_url(tab)

        # 优先用字体文件解析；失败则用 canvas 比对
        mapping = parse_font_file(font_url)
        canvas_map = canvas_digit_recognition(tab, salary_items)

        if not mapping and canvas_map:
            print(f"\n  {INFO} 字体文件解析无结果，使用 canvas 比对结果")
            mapping = canvas_map
        elif mapping and canvas_map:
            # 交叉验证
            conflicts = {cp: (mapping[cp], canvas_map[cp])
                         for cp in mapping if cp in canvas_map
                         and mapping[cp] != canvas_map[cp]}
            if conflicts:
                print(f"\n  {WARN} 字体解析与 canvas 比对存在冲突（以字体解析为准）：")
                for cp, (fa, ca) in conflicts.items():
                    print(f"    U+{cp:04X}: 字体={fa}  canvas={ca}")
            else:
                print(f"\n  [交叉验证] 字体解析与 canvas 比对结果一致 ({len(mapping)} 个)")

        decode_salaries(salary_items, mapping)
        print_codepoint_table(salary_items)

        sep("总结")
        if mapping:
            print(f"  {OK} 成功建立映射表（{len(mapping)} 个码点）")
            print(f"\n  下一步：将映射表硬编码到 scanner.py，或在每次运行时")
            print(f"  动态调用 parse_font_file() 建立映射，再解析 .job-salary 文字。")
            print(f"\n  映射表（Python dict）：")
            print(f"  SALARY_MAP = {{")
            for cp in sorted(mapping):
                print(f"      0x{cp:04X}: {mapping[cp]},   # U+{cp:04X}")
            print(f"  }}")
        else:
            print(f"  {WARN} 未能建立映射表")
            print(f"  建议：安装 fonttools（pip install fonttools brotli）后重跑")
    finally:
        try:
            tab.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
