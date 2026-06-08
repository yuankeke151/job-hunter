"""
page_js.py — scanner 模块用到的全部 JS 脚本常量集中存放（纯字符串，零逻辑）
"""

# ── JS：提取所有卡片字段 ──────────────────────────────────────────────────────
JS_EXTRACT_CARDS = """
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
JS_CARD_RECT = """
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
JS_READ_JD = """
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
JS_READ_CITY = """
(function() {
    const el = document.querySelector('.job-detail-header .tag-list li:first-child a')
            || document.querySelector('.job-detail-header .tag-list li:first-child');
    return el ? el.innerText.trim() : '';
})()
"""

# ── JS：读取右侧 JD 面板当前展示的公司名（用于校验点击是否命中正确卡片）────────
JS_PANEL_COMPANY = """
(function() {
    const el = document.querySelector('.job-detail-header .company-info .name')
            || document.querySelector('.job-detail-header .name')
            || document.querySelector('.company-info .name');
    return el ? el.innerText.trim() : '';
})()
"""

# ── JS：从右侧 JD 面板读取招聘者姓名/title（与 chat 侧「查看职位」详情页同款逻辑）──
JS_PANEL_RECRUITER = """
(function() {
    const boss = document.querySelector('.job-boss-info');
    if (!boss) return JSON.stringify({ recruiterName: '', recruiterTitle: '' });

    let recruiterName = '', recruiterTitle = '';
    const nameEl = boss.querySelector('h2.name');
    if (nameEl) {
        for (const node of nameEl.childNodes) {
            if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
                recruiterName = node.textContent.trim();
                break;
            }
        }
    }
    const attrText = (boss.querySelector('.boss-info-attr')?.innerText || '').trim();
    const parts = attrText.split('\\u00b7').map(s => s.trim()).filter(Boolean);
    if (parts.length >= 2) recruiterTitle = parts[1];

    return JSON.stringify({ recruiterName, recruiterTitle });
})()
"""

# ── JS：获取「立即沟通」按钮坐标 ─────────────────────────────────────────────
JS_CHAT_BTN_RECT = """
(function() {
    const btn = document.querySelector('.op-btn-chat');
    if (!btn) return null;
    const r = btn.getBoundingClientRect();
    return JSON.stringify({ x: r.left + r.width/2, y: r.top + r.height/2,
                            visible: btn.offsetParent !== null });
})()
"""

# ── JS：获取弹窗「留在此页」按钮坐标（class: cancel-btn）────────────────────
JS_STAY_BTN_RECT = """
(function() {
    const btn = document.querySelector('.cancel-btn');
    if (!btn || btn.offsetParent === null) return null;
    const r = btn.getBoundingClientRect();
    if (r.width === 0) return null;
    return JSON.stringify({ x: r.left + r.width/2, y: r.top + r.height/2 });
})()
"""

# ── JS：滚动到页面底部，触发无限滚动加载 ─────────────────────────────────────
JS_SCROLL_BOTTOM = "window.scrollTo(0, document.documentElement.scrollHeight)"

# ── JS：找到「数据分析师」求职期望 tab 的坐标 ─────────────────────────────────
JS_EXPECT_TAB_RECT = """
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
