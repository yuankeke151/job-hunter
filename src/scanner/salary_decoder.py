"""
salary_decoder.py — 扫描页 .job-salary 薪资解码（kanzhun-mix 字体混淆）

BOSS直聘用 kanzhun-mix 自定义字体把薪资数字渲染成私用区 Unicode
（已观测范围 U+E031–U+E03A，共 10 个码点），真实数字与码点的对应关系
在每次页面加载时动态重排，但在同一页面会话内保持稳定
（debug_salary.py 已验证：同一映射表可正确解码全部 16 个薪资元素）。

解码方案：canvas 像素比对 —— 用页面同款字体把每个私用区码点和参考数字
0-9 分别渲染到小画布，逐像素求 MSE，取最接近的数字作为该码点的真实值。

只在程序运行期间建立一次映射表（首次调用 decode 时懒加载触发，覆盖固定
码点范围 U+E031–U+E03A 共 10 个数字），之后全程复用缓存查表解码，
不再产生任何 canvas 渲染 / CDP 调用。
"""
import json
import base64

from shared.logger import log

# 已观测的私用区码点范围：U+E031–U+E03A，对应数字 0-9 的某种动态排列
_PRIVATE_RANGE = range(0xE031, 0xE03B)

# None = 尚未建立；建立后缓存，程序运行期间全程复用
_mapping: dict[int, int] | None = None

_JS_GET_FONT = """
(function() {
    const el = document.querySelector('.job-salary');
    return el ? window.getComputedStyle(el).fontFamily : '"kanzhun-mix",sans-serif';
})()
"""

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
    let s = '';
    for (let i = 0; i < data.length; i += 4)
        s += String.fromCharCode(255 - data[i]);
    return btoa(s);
})(%s, %s)
"""


def _eval(tab, js: str):
    try:
        raw = tab.call_method("Runtime.evaluate", expression=js,
                              returnByValue=True, timeout=10)
        return raw.get("result", {}).get("value")
    except Exception as e:
        log.debug(f"  [薪资] JS 执行失败: {e}")
        return None


def _render_char(tab, ch: str, font: str) -> list[int] | None:
    js = _JS_RENDER_ONE % (json.dumps(ch), json.dumps(font))
    b64 = _eval(tab, js)
    if b64:
        return list(base64.b64decode(b64))
    return None


def _mse(a: list[int], b: list[int]) -> float:
    if not a:
        return float("inf")
    return sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)


def _build_mapping(tab) -> dict[int, int]:
    """对 U+E031–U+E03A 做一次 canvas 像素比对，建立完整 码点→数字 映射。"""
    font = _eval(tab, _JS_GET_FONT) or '"kanzhun-mix",sans-serif'
    log.info(f"  [薪资] 首次运行，建立私用区→数字映射表（字体: {font}）...")

    ref_pixels = [_render_char(tab, d, font) for d in "0123456789"]
    valid_refs = [(d, p) for d, p in enumerate(ref_pixels) if p is not None]
    if not valid_refs:
        log.warning("  [薪资] 参考数字渲染失败，本次运行将不解析薪资")
        return {}

    mapping: dict[int, int] = {}
    for cp in _PRIVATE_RANGE:
        pixels = _render_char(tab, chr(cp), font)
        if pixels is None:
            continue
        best_digit = min(valid_refs, key=lambda dp: _mse(pixels, dp[1]))[0]
        mapping[cp] = best_digit

    log.info(f"  [薪资] 映射建立完成，共 {len(mapping)}/{len(_PRIVATE_RANGE)} 个码点 "
             f"→ {{{', '.join(f'U+{cp:04X}:{d}' for cp, d in sorted(mapping.items()))}}}")
    return mapping


def decode(tab, raw_text: str) -> tuple[str, bool]:
    """
    解码薪资文字。首次调用时建立映射表（程序运行期间仅此一次），之后查表复用。

    返回 (解码结果, 是否完全成功)：
      - 完全成功：所有私用区码点均命中映射表，结果为纯明文（如 "25-50K·14薪"）
      - 不成功：映射表为空，或存在未命中的私用区码点（结果中保留原始码点字符）
    """
    global _mapping
    if not raw_text:
        return "", False

    if _mapping is None:
        _mapping = _build_mapping(tab)

    result = []
    unknown = []
    for ch in raw_text:
        cp = ord(ch)
        if cp in _mapping:
            result.append(str(_mapping[cp]))
        elif 0xE000 <= cp <= 0xF8FF:
            unknown.append(cp)
            result.append(ch)
        else:
            result.append(ch)

    if unknown:
        log.warning(f"  [薪资] 解码失败，未映射私用区码点: "
                    f"{[f'U+{cp:04X}' for cp in unknown]}  原文: {raw_text!r}")

    ok = not unknown and bool(_mapping)
    return "".join(result), ok
