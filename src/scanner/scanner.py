"""
job_scanner.py — 岗位信息获取、AI 匹配度分析、点击立即沟通（含无限滚动翻页）

使用纯 CDP（pychrome）连接已登录 Chrome，不注入 Playwright 运行时。
遍历当前页岗位卡片 → 滚动加载更多 → 点击卡片读取 JD → AI 分析 → 发起沟通 → 写库。
连续两次滚动后无新卡片则判定到达末页，停止。
"""
import sys
import json
import time
import random
import requests
import pychrome
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scanner import analyzer, salary_decoder
from config import CDP_SCANNER_URL, SCAN_API_ENABLED, SCAN_GREET_ENABLED
from shared.database import init_db, get_job_by_content, save_job
from shared.cdp_utils import evaluate, cdp_click, cdp_wheel, random_delay, small_human_scroll, is_browser_alive
from shared.logger import log

CDP_URL        = CDP_SCANNER_URL   # port 9222，由 start_chrome_job.bat 启动
SCROLL_DELTA   = 2000   # 每次滚动像素
SCROLL_WAIT    = 2.5    # 滚动后等待加载秒数
STALE_LIMIT    = 2      # 连续无新卡片次数达到此值则停止
MAX_SCAN       = 100    # 单次运行最多扫描岗位数量
TARGET_CITY    = "北京"  # 目标城市，非此城市只入库不解析

# ── JS：提取所有卡片字段 ──────────────────────────────────────────────────────
_JS_EXTRACT_CARDS = """
(function() {
    const cards = document.querySelectorAll('.job-card-wrap');
    const result = Array.from(cards).map((card, idx) => {
        const q = (sel) => { const e = card.querySelector(sel); return e ? e.innerText.trim() : ''; };

        const jobTags = Array.from(card.querySelectorAll('.tag-list li'))
                            .map(e => e.innerText.trim());

        const salaryEl = card.querySelector('.job-salary');
        const compTags = Array.from(card.querySelectorAll(
            '.company-tag-list li, [class*="company-tag"] li, [class*="company-tag"] span'
        )).map(e => e.innerText.trim());

        const link  = card.querySelector("a[href*='/job_detail/']");
        const href  = link ? link.getAttribute('href') : '';
        const match = href.match(/\\/job_detail\\/([^.?/]+)/);

        return {
            idx,
            name        : q('.job-name')         || q('[class*="job-name"]'),
            company     : q('.boss-info .boss-name') || q('.boss-name'),
            experience  : jobTags[0] || '',
            education   : jobTags[1] || '',
            salary_raw  : salaryEl ? (salaryEl.innerText || '').trim() : '',
            company_size: compTags.find(t => t.includes('人')) || compTags[1] || compTags[0] || '',
            job_id      : match ? match[1] : '',
        };
    });
    return JSON.stringify(result);
})()
"""

# ── JS：获取第 N 个卡片的屏幕中心坐标 ───────────────────────────────────────
_JS_CARD_RECT = """
(function() {{
    const cards = document.querySelectorAll('.job-card-wrap');
    const el = cards[{idx}];
    if (!el) return null;
    el.scrollIntoView({{ block: 'center', behavior: 'instant' }});
    const r = el.getBoundingClientRect();
    return JSON.stringify({{ x: r.left + r.width/2, y: r.top + r.height/2 }});
}})()
"""

# ── JS：读取右侧详情面板 JD（DOM 遍历，以 h3.title 为锚点，跳过头尾噪音）────────
_JS_READ_JD = """
(function() {
    const body = document.querySelector('.job-detail-body');
    if (!body) return '';

    const h3 = body.querySelector('h3.title');
    if (!h3) return body.innerText.trim();   // 兜底：无 h3 则返回全文

    const TAIL_CLS  = ['boss-info', 'detail-op', 'work-addr', 'job-link', 'job-tools', 'hot-link'];
    const TAIL_TEXT = ['去App', '与BOSS随时沟通', '工作地址', '查看更多信息'];

    const parts = [];
    let el = h3.nextElementSibling;
    while (el) {
        const cls  = (el.className || '').toString();
        const text = (el.innerText  || '').trim();

        if (TAIL_CLS.some(c => cls.includes(c))) break;
        if (TAIL_TEXT.some(t => text.includes(t))) break;

        if (text) parts.push(text);
        el = el.nextElementSibling;
    }
    return parts.join('\\n');
})()
"""

# ── JS：从右侧 JD header 的 tag-list 第一项读取城市 ──────────────────────────
_JS_READ_CITY = """
(function() {
    const el = document.querySelector('.job-detail-header .tag-list li:first-child a')
            || document.querySelector('.job-detail-header .tag-list li:first-child');
    return el ? el.innerText.trim() : '';
})()
"""

# ── JS：读取右侧 JD 面板当前展示的公司名（用于校验点击是否命中正确卡片）────────
_JS_PANEL_COMPANY = """
(function() {
    const el = document.querySelector('.job-detail-header .company-info .name')
            || document.querySelector('.job-detail-header .name')
            || document.querySelector('.company-info .name');
    return el ? el.innerText.trim() : '';
})()
"""

# ── JS：获取「立即沟通」按钮坐标 ─────────────────────────────────────────────
_JS_CHAT_BTN_RECT = """
(function() {
    const btn = document.querySelector('.op-btn-chat');
    if (!btn) return null;
    const r = btn.getBoundingClientRect();
    return JSON.stringify({ x: r.left + r.width/2, y: r.top + r.height/2,
                            visible: btn.offsetParent !== null });
})()
"""

# ── JS：获取弹窗「留在此页」按钮坐标（class: cancel-btn）────────────────────
_JS_STAY_BTN_RECT = """
(function() {
    const btn = document.querySelector('.cancel-btn');
    if (!btn || btn.offsetParent === null) return null;
    const r = btn.getBoundingClientRect();
    if (r.width === 0) return null;
    return JSON.stringify({ x: r.left + r.width/2, y: r.top + r.height/2 });
})()
"""


# ── JS：滚动到页面底部，触发无限滚动加载 ─────────────────────────────────────
_JS_SCROLL_BOTTOM = "window.scrollTo(0, document.documentElement.scrollHeight)"

# ── JS：找到「数据分析师」求职期望 tab 的坐标 ─────────────────────────────────
_JS_EXPECT_TAB_RECT = """
(function() {
    for (const el of document.querySelectorAll('.expect-item')) {
        const text = (el.innerText || '').trim();
        if (text.includes('数据分析师') && el.offsetParent !== null) {
            const r = el.getBoundingClientRect();
            return JSON.stringify({ x: Math.round(r.left + r.width/2),
                                    y: Math.round(r.top  + r.height/2) });
        }
    }
    return null;
})()
"""

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def scroll_for_more(tab) -> int:
    """向下滚动一次并等待加载，返回滚动后的卡片总数。"""
    cdp_click_scroll(tab, 760, 400)
    evaluate(tab, _JS_SCROLL_BOTTOM)
    time.sleep(SCROLL_WAIT)
    raw = evaluate(tab, _JS_EXTRACT_CARDS)
    return len(json.loads(raw)) if raw else 0


def cdp_click_scroll(tab, x: float, y: float):
    """发送 mouseWheel 事件触发无限滚动翻页。"""
    cdp_wheel(tab, x, y, SCROLL_DELTA)


def divider():
    log.info("-" * 72)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def scan_page():

    init_db()

    # ── 1. 找到 BOSS 标签页 ───────────────────────────────────────────────────
    log.info(f"[连接] {CDP_URL}")
    try:
        tabs_info = requests.get(f"{CDP_URL}/json", timeout=5).json()
    except Exception as e:
        log.error(f"[失败] 无法连接: {e}"); sys.exit(1)

    boss_info = next(
        (t for t in tabs_info if "zhipin.com" in t.get("url", "") and t.get("type") == "page"),
        None,
    )
    if not boss_info:
        log.error("[失败] 未找到 BOSS直聘 标签页"); sys.exit(1)

    log.info(f"[标签页] {boss_info['title'][:60]}")
    log.info(f"[URL]    {boss_info['url']}")

    # ── 2. pychrome 连接 ──────────────────────────────────────────────────────
    browser = pychrome.Browser(url=CDP_URL)
    tab = next((t for t in browser.list_tab() if t.id == boss_info["id"]), None)
    if not tab:
        log.error("[失败] pychrome 找不到标签页"); sys.exit(1)

    tab.start()
    log.info("[CDP] 连接成功")

    try:
        # ── 3. 遍历卡片（含无限滚动翻页）────────────────────────────────────────
        passed, errors = [], []
        greeted        = 0
        processed_idxs = set()   # 已处理的卡片 idx，防止重复
        stale_count    = 0       # 连续滚动无新卡片次数

        while True:
            if not is_browser_alive(CDP_URL):
                log.info("[退出] 检测到浏览器已关闭，停止扫描")
                break
            if len(processed_idxs) >= MAX_SCAN:
                log.info(f"[退出] 已扫描 {MAX_SCAN} 个岗位，停止运行")
                break

            raw = evaluate(tab, _JS_EXTRACT_CARDS)
            if not raw:
                break
            all_cards = json.loads(raw)
            new_cards = [c for c in all_cards if c["idx"] not in processed_idxs]

            if not new_cards:
                # 当前所有卡片都已处理，尝试滚动加载更多
                prev_total = len(all_cards)
                log.info(f"[翻页] 当前 {prev_total} 张已全部处理，尝试滚动加载...")
                new_total = scroll_for_more(tab)

                if new_total > prev_total:
                    stale_count = 0
                    log.info(f"[翻页] 加载了 {new_total - prev_total} 张新卡片（共 {new_total} 张）")
                else:
                    stale_count += 1
                    log.info(f"[翻页] 无新卡片（{stale_count}/{STALE_LIMIT}）")
                    if stale_count >= STALE_LIMIT:
                        log.info("[翻页] 已到末页，停止扫描")
                        break
                continue

            stale_count = 0
            log.info(f"[提取] 本轮 {len(new_cards)} 张新卡片（已处理 {len(processed_idxs)} 张）")
            log.info("=" * 72)

            for card in new_cards:
                processed_idxs.add(card["idx"])

                idx          = card["idx"]
                name         = card["name"]         or "(无)"
                company      = card["company"]      or "(无)"
                experience   = card["experience"]   or "(无)"
                company_size = card["company_size"] or "(无)"

                salary, salary_ok = salary_decoder.decode(tab, card.get("salary_raw", ""))

                log.info(f"[{idx+1:02d}] {name}  ·  {company}")
                log.info(f"      经验: {experience}  规模: {company_size}")
                if salary:
                    log.info(f"      薪资: {salary}{'' if salary_ok else '（解码不完整）'}")

                # ── 获取卡片坐标并用 CDP 鼠标事件点击 ────────────────────────
                try:
                    small_human_scroll(tab)
                    rect_raw = evaluate(tab, _JS_CARD_RECT.format(idx=idx))
                    if not rect_raw:
                        log.warning(f"      → [跳过] 卡片 DOM 不存在（idx={idx}）")
                        divider()
                        continue
                    rect = json.loads(rect_raw)
                    cdp_click(tab, rect["x"], rect["y"])
                    random_delay(1.5, 2.5)

                    # ── 方案B：校验面板公司名，防止点击错位导致 JD 错配 ──────
                    panel_company = evaluate(tab, _JS_PANEL_COMPANY) or ""
                    if panel_company and company not in panel_company and panel_company not in company:
                        log.warning(f"      → [跳过] 面板公司({panel_company!r}) ≠ 卡片公司({company!r})，点击错位")
                        divider()
                        random_delay(1, 3)
                        continue

                    jd   = evaluate(tab, _JS_READ_JD)   or ""
                    city = evaluate(tab, _JS_READ_CITY) or ""
                    if jd:
                        preview = jd[:120].replace("\n", " ")
                        log.info(f"      JD({len(jd)}字): {preview}...")
                    else:
                        log.warning("      JD: (未获取到)")
                    if city:
                        log.info(f"      城市: {city}")

                    # ── 非目标城市：DB去重后只入库，跳过解析和沟通 ───────────
                    if city and city != TARGET_CITY:
                        log.info(f"      [城市] {city} ≠ {TARGET_CITY}，跳过解析")
                        if jd:
                            if get_job_by_content(name, company, jd):
                                log.debug("      [DB] 已存储，跳过")
                            else:
                                rowid = save_job(
                                    job_id       = card.get("job_id", ""),
                                    company      = company,
                                    position     = name,
                                    jd           = jd,
                                    experience   = card.get("experience", ""),
                                    education    = card.get("education", ""),
                                    company_size = card.get("company_size", ""),
                                    salary       = salary,
                                    salary_ok    = 1 if salary_ok else 0,
                                    city         = city,
                                )
                                log.info(f"      [DB] 已保存 (id={rowid}, 非目标城市)")
                        divider()
                        random_delay(1, 3)
                        continue

                    # ── DB 去重：已存储过则跳过 ───────────────────────────────
                    if jd and get_job_by_content(name, company, jd):
                        log.debug("      [DB] 已存储，跳过 → 下一岗位")
                        divider()
                        random_delay(1, 3)
                        continue

                    # ── AI 匹配度分析 ─────────────────────────────────────────
                    analysis     = {}
                    should_apply = False
                    score        = 0
                    if jd and SCAN_API_ENABLED:
                        log.info("      [分析] 调用 Claude API...")
                        analysis     = analyzer.analyze_job(
                            company, name, jd,
                            salary = salary if salary_ok else "",
                        )
                        score        = analysis["match_score"]
                        should_apply = analysis["should_apply"]
                        key_matches  = analysis["key_matches"]
                        missing      = analysis["missing_skills"]
                        skip_reason  = analysis["skip_reason"]

                        verdict = "✓ 推荐投递" if should_apply else "✗ 跳过"
                        log.info(f"      匹配分: {score}/100  {verdict}")
                        if key_matches:
                            log.info(f"      匹配点: {' | '.join(key_matches)}")
                        if missing:
                            log.info(f"      缺失项: {' | '.join(missing)}")
                        if not should_apply and skip_reason:
                            log.info(f"      跳过原因: {skip_reason}")
                    elif jd:
                        log.info("      [分析] SCAN_API_ENABLED=False，跳过 API 分析")

                    # ── 立即沟通 ─────────────────────────────────────────────
                    # greet_status: 0=未打招呼  1=本次打招呼  2=他端已打招呼
                    greet_status = 0
                    if should_apply and not SCAN_GREET_ENABLED:
                        log.info("      [沟通] SCAN_GREET_ENABLED=False，跳过打招呼")
                    elif should_apply:
                        log.info("      [沟通] 尝试点击「立即沟通」...")
                        btn_raw = evaluate(tab, _JS_CHAT_BTN_RECT)
                        if not btn_raw:
                            log.warning("      [沟通] 未找到「立即沟通」按钮，跳过")
                        elif not json.loads(btn_raw).get("visible"):
                            log.warning("      [沟通] 按钮不可见，跳过")
                        else:
                            btn        = json.loads(btn_raw)
                            url_before = evaluate(tab, "window.location.href") or ""
                            cdp_click(tab, btn["x"], btn["y"])
                            random_delay(1.0, 1.5)

                            url_after = evaluate(tab, "window.location.href") or ""
                            stay_raw  = evaluate(tab, _JS_STAY_BTN_RECT)

                            if stay_raw:
                                stay = json.loads(stay_raw)
                                cdp_click(tab, stay["x"], stay["y"])
                                log.info("      [弹窗] 已点击「留在此页」")
                                random_delay(0.5, 1.0)
                                greet_status = 1

                            elif url_after != url_before:
                                log.info("      [跳转] 检测到页面跳转 → 他端已打过招呼")
                                log.info("      [返回] 导航回岗位列表...")
                                tab.call_method("Page.navigate", url=url_before, timeout=15)
                                random_delay(2.5, 3.5)

                                # 页面回到推荐 tab，需点击求职期望 tab 刷新列表
                                tab_raw = evaluate(tab, _JS_EXPECT_TAB_RECT)
                                if tab_raw:
                                    t = json.loads(tab_raw)
                                    cdp_click(tab, t["x"], t["y"])
                                    log.info("      [返回] 已点击「数据分析师」tab，等待列表刷新...")
                                    random_delay(2.0, 2.5)
                                else:
                                    log.warning("      [返回] 未找到求职期望 tab，列表可能停在推荐页")

                                log.info("      [返回] 已回到岗位列表")
                                greet_status = 2

                            else:
                                log.warning("      [沟通] 点击后无响应，跳过")

                        if greet_status > 0:
                            greeted += 1
                            label = "本次打招呼" if greet_status == 1 else "他端已打招呼"
                            log.info(f"      [沟通] {label}（本次共 {greeted} 个）")

                    # ── 写入数据库 ────────────────────────────────────────────
                    if greet_status == 2:
                        analyzed_val = 2
                    elif analysis:
                        analyzed_val = 1
                    else:
                        analyzed_val = 0

                    if jd:
                        rowid = save_job(
                            job_id         = card.get("job_id", ""),
                            company        = company,
                            position       = name,
                            jd             = jd,
                            experience     = card.get("experience", ""),
                            education      = card.get("education", ""),
                            company_size   = card.get("company_size", ""),
                            salary         = salary,
                            salary_ok      = 1 if salary_ok else 0,
                            city           = city,
                            analyzed       = analyzed_val,
                            score          = analysis.get("match_score", 0),
                            should_apply   = 1 if analysis.get("should_apply") else 0,
                            key_matches    = analysis.get("key_matches", []),
                            missing_skills = analysis.get("missing_skills", []),
                            skip_reason    = analysis.get("skip_reason", ""),
                            greeted        = greet_status,
                        )
                        log.info(f"      [DB] 已保存 (id={rowid}, analyzed={analyzed_val}, greeted={greet_status})")

                    passed.append({**card, "status": "passed", "jd": jd, "analysis": analysis})

                except Exception as e:
                    log.exception(f"      → [异常] 跳过: {e}")
                    errors.append({**card, "status": "error", "jd": ""})

                divider()
                if len(processed_idxs) >= MAX_SCAN:
                    log.info(f"[退出] 已扫描 {MAX_SCAN} 个岗位，停止运行")
                    break
                random_delay(1, 3)

        # ── 4. 汇总 ───────────────────────────────────────────────────────────
        log.info("=" * 72)
        recommended = [r for r in passed if r.get("analysis", {}).get("should_apply")]
        log.info(f"扫描完成，共处理 {len(processed_idxs)} 个岗位：")
        log.info(f"  成功获取 JD: {len(passed):>3} 个")
        log.info(f"  推荐投递:    {len(recommended):>3} 个")
        log.info(f"  已发起沟通:  {greeted:>3} 个")
        log.info(f"  异常跳过:    {len(errors):>3} 个")

        if recommended:
            log.info("推荐投递岗位：")
            for r in recommended:
                score = r["analysis"]["match_score"]
                log.info(f"  ★ [{score:>3}分] {r['name']:<28} | {r['company']}")

    finally:
        try:
            tab.stop()
        except Exception:
            pass


if __name__ == "__main__":
    scan_page()
