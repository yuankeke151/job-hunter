"""
job_scanner.py — 岗位信息获取、AI 匹配度分析、点击立即沟通（含无限滚动翻页）

使用纯 CDP（pychrome）连接已登录 Chrome，不注入 Playwright 运行时。
遍历当前页岗位卡片 → 滚动加载更多 → 点击卡片读取 JD → AI 分析 → 发起沟通 → 写库。
连续两次滚动后无新卡片则判定到达末页，停止。
"""
import sys
import requests
import pychrome
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scanner import analyzer, salary_decoder, card_extractor, job_detail_reader, greet_action
from config import CDP_SCANNER_URL, SCAN_API_ENABLED, SCAN_GREET_ENABLED, MAX_NEW_JOBS
from shared.database import init_db, get_job_by_encrypt_id, save_job, update_job_by_encrypt_id
from shared.cdp_utils import evaluate, random_delay, is_browser_alive
from shared.logger import log

CDP_URL        = CDP_SCANNER_URL   # port 9222，由 start_chrome_job.bat 启动
STALE_LIMIT    = 2      # 连续无新卡片次数达到此值则停止
TARGET_CITY    = "北京"  # 目标城市，非此城市只入库不解析


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
        greeted_count = 0
        processed_idxs = set()   # 已处理的卡片 idx，防止重复
        new_jobs_count = 0        # 本次新岗位数（数据库中原本无记录的）
        stale_count    = 0       # 连续滚动无新卡片次数

        while True:
            if not is_browser_alive(CDP_URL):
                log.info("[退出] 检测到浏览器已关闭，停止扫描")
                break
            if new_jobs_count >= MAX_NEW_JOBS:
                log.info(f"[退出] 已处理 {MAX_NEW_JOBS} 个新岗位，停止运行")
                break

            all_cards = card_extractor.extract_cards(tab)
            if not all_cards:
                break
            new_cards = [c for c in all_cards if c["idx"] not in processed_idxs]

            if not new_cards:
                # 当前所有卡片都已处理，尝试滚动加载更多
                prev_total = len(all_cards)
                log.info(f"[翻页] 当前 {prev_total} 张已全部处理，尝试滚动加载...")
                new_total = card_extractor.scroll_for_more(tab)

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
                if not is_browser_alive(CDP_URL):
                    log.info("[退出] 检测到浏览器已关闭，停止扫描")
                    break
                if new_jobs_count >= MAX_NEW_JOBS:
                    break
                processed_idxs.add(card["idx"])

                idx          = card["idx"]
                name         = card["name"]         or "(无)"
                company      = card["company"]      or "(无)"
                experience   = card["experience"]   or "(无)"
                company_size = card["company_size"] or "(无)"

                encrypt_job_id = card.get("job_id", "")
                if not encrypt_job_id:
                    log.error(f"      [致命] 未获取到 encryptJobId（idx={idx}），程序终止")
                    sys.exit(1)

                # ── 点击前查库：按条件决定跳过或重新处理 ──
                existing_job = get_job_by_encrypt_id(encrypt_job_id)
                re_process = False  # True=已有记录但需重新处理（更新而非 INSERT）
                if existing_job:
                    ex_city = existing_job.get("city", "")
                    if ex_city and ex_city != TARGET_CITY:
                        log.info(f"[{idx+1:02d}] {name}  ·  {company}  → [DB] 非目标城市({ex_city})，跳过")
                        divider(); random_delay(1, 3); continue
                    if existing_job.get("greeted", 0) != 0:
                        log.info(f"[{idx+1:02d}] {name}  ·  {company}  → [DB] 已打招呼，跳过")
                        divider(); random_delay(1, 3); continue
                    if existing_job.get("should_apply", -1) == 0:
                        log.info(f"[{idx+1:02d}] {name}  ·  {company}  → [DB] 不推荐，跳过")
                        divider(); random_delay(1, 3); continue
                    # 存在但未打招呼且 should_apply != 0（可能是 -1 未分析 或 1 推荐但未沟通成功）
                    re_process = True
                    log.info(f"[{idx+1:02d}] {name}  ·  {company}  → [DB] 已存储(id={existing_job['id']})但未沟通，重新处理")
                else:
                    new_jobs_count += 1

                try:
                    salary, salary_ok = salary_decoder.decode(tab, card.get("salary_raw", ""))
                except Exception:
                    log.warning(f"  薪资解码异常，使用原始值")
                    salary, salary_ok = card.get("salary_raw", ""), 0

                log.info(f"[{idx+1:02d}] {name}  ·  {company}")
                log.info(f"      经验: {experience}  规模: {company_size}")
                if salary:
                    log.info(f"      薪资: {salary}{'' if salary_ok else '（解码不完整）'}")

                # ── 点击卡片，读取详情面板 ────────────────────────────────────
                try:
                    detail = job_detail_reader.read_job_detail(tab, idx, company)
                    if not detail.get("ok"):
                        divider()
                        if detail.get("skip_reason") == "panel_mismatch":
                            random_delay(1, 3)
                        continue

                    jd              = detail["jd"]
                    city            = detail["city"]
                    recruiter_name  = detail["recruiter_name"]
                    recruiter_title = detail["recruiter_title"]

                    if not jd:
                        log.warning("      JD: (未获取到)")
                        divider()
                        random_delay(1, 3)
                        continue

                    preview = jd[:120].replace("\n", " ")
                    log.info(f"      JD({len(jd)}字): {preview}...")
                    if city:
                        log.info(f"      城市: {city}")

                    # ── 非目标城市：DB去重后只入库，跳过解析和沟通 ───────────
                    if city and city != TARGET_CITY:
                        log.info(f"      [城市] {city} ≠ {TARGET_CITY}，跳过解析")
                        if jd:
                            rowid = save_job(
                                job_id       = encrypt_job_id,
                                company      = company,
                                position     = name,
                                jd           = jd,
                                experience   = card.get("experience", ""),
                                education    = card.get("education", ""),
                                company_size = card.get("company_size", ""),
                                salary       = salary,
                                salary_ok    = 1 if salary_ok else 0,
                                city         = city,
                                recruiter_name  = recruiter_name,
                                recruiter_title = recruiter_title,
                            )
                            log.info(f"      [DB] 已保存 (id={rowid}, 非目标城市)")
                        divider()
                        random_delay(1, 3)
                        continue

                    # ── AI 匹配度分析 ─────────────────────────────────────────
                    analysis     = {}
                    should_apply = False
                    score        = 0
                    if jd and SCAN_API_ENABLED:
                        log.info("      [分析] 调用 API...")
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
                    greet_status = 0  # 0=未沟通 1=本次沟通 2=他端已沟通
                    if should_apply and not SCAN_GREET_ENABLED:
                        log.info("      [沟通] SCAN_GREET_ENABLED=False，跳过打招呼")
                    elif should_apply:
                        greet_status = greet_action.try_greet(tab)
                        if greet_status:
                            greeted_count += 1
                            label = "本次打招呼" if greet_status == 1 else "他端已沟通"
                            log.info(f"      [沟通] {label}（本次共 {greeted_count} 个）")

                    # ── 写入数据库 ────────────────────────────────────────────
                    db_score       = analysis.get("match_score", -1) if analysis else -1
                    db_should_apply = -1
                    if analysis and db_score != -1:
                        db_should_apply = 1 if analysis.get("should_apply") else 0
                    if re_process:
                        update_job_by_encrypt_id(
                            encrypt_job_id,
                            jd=jd, experience=card.get("experience", ""),
                            education=card.get("education", ""),
                            company_size=card.get("company_size", ""),
                            salary=salary, salary_ok=1 if salary_ok else 0,
                            city=city, recruiter_name=recruiter_name,
                            recruiter_title=recruiter_title,
                            greeted=greet_status,
                            score=db_score, should_apply=db_should_apply,
                            key_matches=analysis.get("key_matches", []),
                            missing_skills=analysis.get("missing_skills", []),
                            skip_reason=analysis.get("skip_reason", ""),
                        )
                        log.info(f"      [DB] 已更新 (id={existing_job['id']})")
                    else:
                        rowid = save_job(
                            job_id         = encrypt_job_id,
                            company        = company,
                            position       = name,
                            jd             = jd,
                            experience     = card.get("experience", ""),
                            education      = card.get("education", ""),
                            company_size   = card.get("company_size", ""),
                            salary         = salary,
                            salary_ok      = 1 if salary_ok else 0,
                            city           = city,
                            recruiter_name  = recruiter_name,
                            recruiter_title = recruiter_title,
                            greeted        = greet_status,
                            score          = db_score,
                            should_apply   = db_should_apply,
                            key_matches    = analysis.get("key_matches", []),
                            missing_skills = analysis.get("missing_skills", []),
                            skip_reason    = analysis.get("skip_reason", ""),
                        )
                        log.info(f"      [DB] 已保存 (id={rowid})")

                    passed.append({**card, "status": "passed", "jd": jd, "analysis": analysis})

                except Exception as e:
                    log.exception(f"      → [异常] 跳过: {e}")
                    errors.append({**card, "status": "error", "jd": ""})

                divider()
                random_delay(1, 3)

        # ── 4. 汇总 ───────────────────────────────────────────────────────────
        log.info("=" * 72)
        recommended = [r for r in passed if r.get("analysis", {}).get("should_apply")]
        log.info(f"扫描完成，共处理 {len(processed_idxs)} 个岗位（其中新岗位 {new_jobs_count} 个）：")
        log.info(f"  成功获取 JD: {len(passed):>3} 个")
        log.info(f"  推荐投递:    {len(recommended):>3} 个")
        log.info(f"  已发起沟通:  {greeted_count:>3} 个")
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
