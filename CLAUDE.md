# Job Hunter — 项目说明

## 项目概述

基于 Python + Chrome CDP + Claude API + SQLite 的 BOSS直聘自动化求职脚本。使用 Chrome 调试模式（远程调试端口 9222）+ pychrome 控制浏览器，绕过 BOSS直聘反爬检测，自动完成岗位扫描、AI 匹配分析、发起沟通、聊天回复全流程。

两个模块**共用同一个 Chrome 实例**（`start_chrome.bat` 启动，端口 9222，用户数据目录 `browser_data/`），分别在不同标签页中操作：
- `scanner/scanner.py`：扫描职位列表，AI 匹配，发起沟通 → 操作 zhipin.com 职位列表标签页
- `chat/handler.py`：处理 IM 聊天，发简历，AI 回复 → 操作 `/web/geek/chat` 标签页

---

## 技术方案

### 浏览器控制

**操作 BOSS 直聘页面不使用 Playwright**，原因：
- `connect_over_cdp` 会向页面注入运行时，触发 BOSS 检测并跳转到 `about:blank`
- Playwright Chromium 的 TLS 指纹、Canvas 渲染特征与真实 Chrome 不同，页面加载即被识别

**正确方案**：用 `start_chrome.bat` 启动系统 Chrome（`--remote-debugging-port=9222`），用户手动登录后，脚本通过 `pychrome` + CDP 连接操作。

> 例外：`chat/resume_tailor.py` 在生成定制简历 PDF 时会启动**独立的无头 Playwright Chromium**
> （`sync_playwright().chromium.launch()`）将 HTML 渲染为 PDF——这与 BOSS 反爬无关，
> 该浏览器实例从不连接 zhipin.com，纯粹利用 Playwright 的 `page.pdf()` 做离线排版渲染。
> `requirements.txt` 中的 `playwright` 依赖即为此用途（首次使用需额外执行
> `playwright install chromium`）。

```
双击 start_chrome.bat
  → 手动导航到 zhipin.com 并登录
  → python src/scanner/scanner.py    # 开始扫描
```

### CDP 操作规范

- **标签页查找**：`requests.get("http://localhost:9222/json")` 找到 `zhipin.com` 的 page 类型标签页
- **JS 执行**：`tab.call_method("Runtime.evaluate", expression=js, returnByValue=True)`
- **鼠标点击**：必须用 `Input.dispatchMouseEvent`（mousePressed + mouseReleased），`element.click()` 不触发 React 合成事件
- **元素坐标**：`element.getBoundingClientRect()` 取屏幕坐标后传给 CDP

```python
def cdp_click(tab, x, y):
    common = dict(x=x, y=y, button="left", clickCount=1, modifiers=0)
    tab.call_method("Input.dispatchMouseEvent", type="mousePressed",  **common)
    tab.call_method("Input.dispatchMouseEvent", type="mouseReleased", **common)
```

### 反爬说明

- **薪资字段（scanner 列表页）**：`.job-salary` 用 `kanzhun-mix` 自定义字体混淆薪资数字（私用区 Unicode U+E031–U+E03A），读取到乱码。**已实现解码**：见 `src/scanner/salary_decoder.py`，用 canvas 像素比对（页面同款字体逐字符渲染后与参考数字 0-9 比 MSE 取最近邻）建立私用区码点→数字映射；映射在同一页面会话内保持稳定，因此整个建表过程**程序运行期间仅懒加载执行一次**（模块级缓存 `_mapping`），后续所有薪资文本直接查表解码。解码结果与成功标志写入 `jobs.salary` / `jobs.salary_ok`；解码成功时还会随同 JD 一并发给 AI 匹配分析（`analyzer.analyze_job` 的 `salary` 参数）
- **薪资字段（chat 聊天页）**：聊天侧不再单独从 `window.chat.communicating.salaryDesc`/`lowSalary`/`highSalary` 读取并入库（`_JS_CHAT_INFO` / `chats` 表均已移除这些字段）。薪资改为与 JD 走同一条数据链路：命中 `jobs` 表时直接复用 `jobs.salary`（即 scanner 侧已解码的薪资描述），未命中、走「查看职位」补录时取 `fetch_job_detail_via_view_job` 返回的明文 `detail["salary"]`（与 `detail["jd"]` 同时获取并写入 `jobs.salary`，见 `save_job_from_view_detail`）。`session_processor.process_session` 在提取/补录 `jd` 的同时同步取出 `salary`，经 `execute_session_actions` 透传给 `call_ai`，由 `ai.py` 拼接 `salary_line`（写法与 `analyzer.py` 的 `salary_line` 一致）后嵌入 `jd_section` 一并发给 AI
- **页面结构**：BOSS直聘为 React SPA，CSS 类名带版本哈希，选择器可能随版本失效，失效时用 Chrome DevTools 手动定位后更新 `src/scanner/page_js.py` 和 `src/chat/session_actions.py` 中对应的选择器常量

---

## AI API 配置

| 项目 | 值 |
|------|----|
| 接口地址 | 见 `src/confidential.py` |
| API Key | 见 `src/confidential.py` |
| 使用模型 | 见 `src/confidential.py` |
| 协议格式 | OpenAI Chat Completions 兼容，使用 `openai` SDK |

`src/confidential.py` 已加入 `.gitignore`，不上传 GitHub。首次克隆后需手动创建：

```python
# src/confidential.py
API_BASE_URL = "https://..."
API_KEY      = "sk-..."
AI_MODEL     = "claude-sonnet-4-6"
```

`config.py` 通过 `try/except ImportError` 自动加载该文件，覆盖占位符 `"***"`。

---

## 目录结构

```
job-hunter/
├── start_chrome.bat         # Chrome（port 9222）
├── requirements.txt             # 依赖列表
├── src/
│   ├── config.py                # 全局配置（BASE_DIR 指向 job-hunter/）
│   ├── confidential.py          # 敏感配置：API Key 等（.gitignore，不上传）
│   ├── scanner/                 # job_scanner 全部逻辑（已按职责拆分为多个子模块）
│   │   ├── __init__.py
│   │   ├── scanner.py           # 入口：主循环（无限滚动翻页 + 单卡片处理编排 + 写库 + 汇总）
│   │   ├── page_js.py           # scanner 用到的全部 JS 脚本常量集中存放（纯字符串，零逻辑）
│   │   ├── card_extractor.py    # 卡片列表提取（extract_cards）与无限滚动翻页（scroll_for_more）
│   │   ├── job_detail_reader.py # 单卡片详情读取（read_job_detail）：点击卡片→面板公司校验→读取 JD/城市/招聘者信息
│   │   ├── greet_action.py      # 「立即沟通」操作（try_greet）：点击按钮→检测弹窗/跳转/无响应→返回 greet_status
│   │   ├── analyzer.py          # AI 匹配分析（仅 scanner 使用）
│   │   └── salary_decoder.py    # .job-salary 薪资解码（canvas 像素比对，仅 scanner 使用）
│   ├── chat/                    # chat_handler 全部逻辑（已按职责拆分为多个子模块）
│   │   ├── __init__.py
│   │   ├── handler.py           # 入口（原 chat_handler.py）：主循环、CDP 连接、会话列表轮询
│   │   ├── session_processor.py # 会话读取、分析、写库（process_session）
│   │   ├── session_actions.py   # 会话操作主入口/编排（execute_session_actions，场景A/B/C分支）
│   │   ├── ai.py                # AI 回复生成（call_ai、_SYS_PROMPT），自我介绍改用固定文案
│   │   ├── messaging.py         # 输入框打字/发送（clear_and_type、click_send、type_and_log）
│   │   ├── resume_dialog.py     # 默认简历发送弹窗/确认气泡处理（handle_resume_dialog、click_resume_btn）
│   │   ├── resume_attachment.py # 定制简历附件管理全流程（execute_resume_action、上传/删除/会话卡定位）
│   │   ├── resume_tailor.py     # 调用 AI 生成定制简历 PDF（generate_tailored_resume）；AI 输出 HTML 片段（h1/h2/ul/p），Playwright 渲染为带格式 PDF（居中姓名、分节标题带下划线、列表缩进）
│   │   ├── interactive_cards.py # 微信交换/沟通意向等非简历交互卡片「同意」批量处理
│   │   └── job_detail_fetch.py  # 「查看职位」详情页抓取（fetch_job_detail_via_view_job）
│   ├── debug/                   # 调试脚本（探索选择器、验证流程，非生产代码）
│   │   ├── debug_elements_chat.py
│   │   ├── debug_elements_job.py
│   │   ├── debug_recruiter_job.py
│   │   ├── debug_resume_attachment_flow.py
│   │   ├── debug_salary.py
│   │   ├── debug_salary_chat.py
│   │   └── debug_view_job.py
│   └── shared/                  # 两侧共用基础模块
│       ├── __init__.py
│       ├── database.py          # SQLite CRUD（jobs + chats 两张表）
│       ├── cdp_utils.py         # CDP 底层工具函数：evaluate/cdp_click/cdp_wheel/random_delay/small_human_scroll/is_browser_alive/read_messages/scroll_into_view_and_click
│       ├── ai_client.py         # AI Client 封装（get_client，供 scanner.analyzer 与 chat.ai 共用）
│       └── logger.py            # 双路日志：控制台(INFO) + 文件(DEBUG，5MB×3)
├── resume/
│   ├── 袁柯.pdf                 # 原始简历
│   └── 袁柯.txt                 # 简历文本缓存（首次运行自动生成）
├── output_resumes/              # AI 生成的定制简历 PDF（命名「袁柯_公司名称_生成时间.pdf」）
├── screenshots/                 # 截图输出目录
├── records/
│   └── jobs.db                  # SQLite 数据库（jobs + chats 两张表）
├── logs/
│   └── app.log                  # 运行日志
└── browser_data/                # Chrome 用户数据（scanner + chat 共用同一登录状态）
```

> `config.py` 中仍保留 `BROWSER_DATA_CHAT_DIR`（指向 `browser_data_chat/`）常量定义并在启动时建目录，
> 为单 Chrome 实例改造前的遗留项，实际已不再被任何模块使用，可忽略该目录。

**sys.path 规则**：`src/` 各子包文件头部均有 `sys.path.insert(0, str(Path(__file__).parent.parent))`，将 `src/` 加入路径，跨包导入使用绝对路径（`from shared.database import ...`、`from chat.session_processor import ...`）。

---

## 运行参数（scanner 各模块常量 + config.py）

| 常量 | 位置 | 默认值 | 说明 |
|------|------|--------|------|
| `TARGET_CITY` | scanner.py | `"北京"` | 目标城市，非此城市只入库不解析 |
| `STALE_LIMIT` | scanner.py | `2` | 连续无新卡片次数达到此值则判定末页停止 |
| `MAX_NEW_JOBS` | config.py | `50` | 单次运行最多处理的新岗位数（数据库中原本无记录的），达到后停止 |
| `SCAN_API_ENABLED` | config.py | `True` | `True`=调用 AI API 分析匹配度；`False`=跳过，score=0 |
| `SCAN_GREET_ENABLED` | config.py | `True` | `True`=点击「立即沟通」并处理弹窗；`False`=只扫描不打招呼 |
| `SCORE_THRESHOLD` | config.py | `70` | AI 匹配分阈值，≥70 才推荐投递 |
| `SCROLL_DELTA` | scanner/card_extractor.py | `2000` | 每次翻页滚动像素 |
| `SCROLL_WAIT` | scanner/card_extractor.py | `2.5` | 滚动后等待新卡片加载的秒数 |

---

## 已验证的页面选择器（2026-06）

| 元素 | 选择器 |
|------|--------|
| 岗位卡片 | `.job-card-wrap` |
| 职位名称 | `.job-name` |
| 公司名称 | `.boss-info .boss-name` |
| 薪资（混淆） | `.job-salary` |
| 经验/学历标签 | `.tag-list li` |
| 公司规模标签 | `.company-tag-list li` |
| JD 内容容器 | `.job-detail-body` |
| JD header 标签列（城市/经验/学历） | `.job-detail-header .tag-list li` |
| 城市（JD header 第一项） | `.job-detail-header .tag-list li:first-child a` |
| 立即沟通按钮 | `.op-btn-chat` |
| 沟通弹窗「留在此页」 | `.cancel-btn` |
| 求职期望 tab | `.expect-item`（取含"数据分析师"文字的项） |
| JD 面板公司名（错配校验用） | `.job-detail-header .company-info .name`（备选：`.job-detail-header .name` / `.company-info .name`） |
| JD 面板招聘者信息 | `.job-boss-info`（`h2.name` 首文本节点=姓名，`.boss-info-attr` 按"·"拆分第二段=title，与 chat 侧「查看职位」详情页结构相同） |

---

## 主流程逻辑（scanner.py，原 job_scanner.py）

### 1. 启动

初始化数据库（`init_db()`），连接 CDP，找到 BOSS直聘标签页。

### 2. 主循环：无限滚动翻页

外层 `while True` 负责翻页，内层 `for card in new_cards` 处理每张卡片。用 `processed_idxs` 集合记录已处理的卡片 idx，防止重复处理。

**翻页逻辑**：当前所有卡片都已处理后，同时发送 CDP `mouseWheel` 事件和 JS `window.scrollTo(bottom)` 触发加载，等待 `SCROLL_WAIT` 秒。连续 `STALE_LIMIT` 次滚动后卡片数不再增加，判定到达末页，退出循环。

**停止条件**：每次外层循环开头先检测浏览器是否存活（`is_browser_alive`），浏览器关闭时立即退出；新入库岗位数达到 `MAX_NEW_JOBS`（默认 50）时同样退出。打招呼数量不再作为停止条件。

### 3. 单张卡片处理流程

任何步骤抛出异常则记录 `errors` 并跳过，卡片间随机等待 1–3 秒。

#### 步骤一：点击前查库（按 encryptJobId 精确匹配，省去无意义点击）

`page_js.JS_EXTRACT_CARDS`（经 `card_extractor.extract_cards` 调用）已在批量提取卡片时从卡片链接 `/job_detail/<id>` 中正则解析出 `job_id`（即 encryptJobId），**无需点击卡片即可拿到**。

**encryptJobId 致命检查**：若提取结果为空字符串，视为重大异常（正常情况下必定存在），记录 `log.error` 后直接 `sys.exit(1)` 终止程序，不再继续运行。

拿到 `encrypt_job_id` 后，**点击卡片之前**先按其精确查询 `jobs.job_id`（`get_job_by_encrypt_id`，结果记为 `existing_job`，供后续步骤复用，不再重复查询）：

- **未命中**（全新岗位）→ `new_jobs_count += 1`，继续点击卡片，进入步骤二
- **命中**（已存在于 DB）→ 按以下顺序判断是否跳过：
  1. 城市非空且 ≠ `TARGET_CITY` → **跳过**（非目标城市，无需再处理）
  2. `greeted ≠ 0`（已打招呼或他端已沟通）→ **跳过**
  3. `should_apply == 0`（AI 明确判定不推荐）→ **跳过**
  4. 以上均不满足（`greeted=0` 且 `should_apply≠0`，可能是 -1 未分析或 1 推荐但沟通未成功）→ **不跳过**，标记 `re_process=True`，重新点击卡片走分析+沟通流程，最终通过 `update_job_by_encrypt_id` 更新已有记录

#### 步骤二：点击卡片，读取 JD 和城市

点击前先调用 `small_human_scroll(tab)` 模拟人类浏览行为（随机小幅滚动 80–280px）。获取卡片坐标时先执行 `scrollIntoView({block:'center', behavior:'instant'})`，确保卡片在视口内再取 `getBoundingClientRect()` 坐标，避免视口外点击失效导致 JD 错配。CDP 鼠标点击卡片中心坐标，等待 1.5–2.5 秒。

**面板公司校验（防错配）**：点击后从 JD 面板读取公司名（`.job-detail-header .company-info .name`），与卡片列表中的公司名比对，不匹配则跳过该卡片，避免处理旧面板内容写入错误记录。

**JD 提取（DOM 遍历，方案A）**：取 `.job-detail-body`，以 `h3.title` 为锚点（页面固定的结构标题，属于噪音），收集其后所有兄弟节点文本，遇到 `boss-info / detail-op / work-addr` 等 class 或「去App / 工作地址」等文字则停止。不依赖关键词匹配，天然保留正文首行（即使首行是「职位描述」）和「【岗位职责】」等带括号格式。

**城市提取**：读取 `.job-detail-header .tag-list li:first-child a` 的文字（JD header 的标签列第一项始终是城市）。

#### 步骤三：城市过滤

```
city 非空 且 city ≠ TARGET_CITY（"北京"）：
  → save_job（只存基础字段，不做 AI 分析）
  → continue（跳过 AI 分析和沟通）

city == TARGET_CITY 或 city 为空（无法读取时不过滤）：
  → 继续后续步骤
```

#### 步骤四：AI 匹配分析（受 `SCAN_API_ENABLED` 控制）

仅 `SCAN_API_ENABLED=True` 时执行，否则跳过（score=0, should_apply=False）。调用 `analyzer.analyze_job(company, name, jd)`：
1. 读取简历（优先 `resume/袁柯.txt` 缓存，否则解析 PDF 并写缓存）
2. 调用 AI API，System 提示要求只输出 JSON
3. 剥除响应中可能的 markdown 代码块（` ```json ``` `）
4. 解析失败或 API 异常时返回 `score=0, should_apply=False`
5. `should_apply = score >= SCORE_THRESHOLD`

#### 步骤五：立即沟通（受 `SCAN_GREET_ENABLED` 控制）

条件：`should_apply = True` 且 `SCAN_GREET_ENABLED = True`。`SCAN_GREET_ENABLED=False` 时跳过此步骤（`greet_status=0`，只记录推荐，不点击沟通按钮）。记录 `url_before`，CDP 点击 `.op-btn-chat`，等待 1–1.5 秒，检测结果：

| 检测结果 | 含义 | 处理 | greet_status |
|---------|------|------|---|
| `.cancel-btn` 出现 | 首次沟通，弹窗出现 | 点击「留在此页」，等 0.5–1s | 1 |
| URL 发生变化 | 他端已沟通，直接跳转会话列表 | `Page.navigate` 回 `url_before` → 等 2.5–3.5s → 点击「数据分析师」求职期望 tab → 等 2–2.5s | 2 |
| 两者都没有 | 异常，点击无响应 | 跳过 | 0 |

`greet_status` 最终写入 `jobs.greeted` 字段。

**回退后恢复**：`Page.navigate` 回原 URL 后，页面默认停在「推荐」tab，需点击 `.expect-item`（含"数据分析师"文字）切回目标搜索结果，等待列表刷新后继续扫描。

#### 步骤六：写入数据库

条件：JD 非空。

- **全新岗位**（`re_process=False`）→ `save_job(...)`，INSERT 新记录
- **重新处理**（`re_process=True`）→ `update_job_by_encrypt_id(...)`，UPDATE 已有记录的分析结果和沟通状态

写入字段含 `greeted`（0/1/2，来自步骤五的 `greet_status`）、`score` 和 `should_apply`（API 未调用时均为 -1）。

### 4. 汇总输出

打印处理总数 / 成功获取 JD 数 / 推荐投递数 / 已发起沟通数 / 异常跳过数，并列出所有推荐岗位及得分。

---

## 数据库字段说明（records/jobs.db）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER | 主键，自增 |
| `job_id` | TEXT | BOSS直聘 URL 中的岗位 ID（可能为空） |
| `company` | TEXT | 公司名称 |
| `position` | TEXT | 岗位名称 |
| `jd` | TEXT | 职位描述全文（已去噪） |
| `experience` | TEXT | 经验要求 |
| `education` | TEXT | 学历要求 |
| `company_size` | TEXT | 公司规模 |
| `salary` | TEXT | 解码后的薪资描述（如 "25-50K·14薪"），解码失败时存原始乱码 |
| `salary_ok` | INTEGER | 薪资解码是否完全成功：0=失败/未知 1=成功 |
| `city` | TEXT | 城市（从 JD header 提取，或聊天页「查看职位」详情页提取） |
| `recruiter_name` | TEXT | 招聘者姓名（scanner 来自 JD 详情面板 `.job-boss-info`，chat 来自「查看职位」详情页，二者结构相同） |
| `recruiter_title` | TEXT | 招聘者 title，如"HRBP"（来源同上） |
| `source` | TEXT | 岗位来源：`scanner`=扫描页 `chat`=聊天页「查看职位」补录 |
| `greeted` | INTEGER | 0=未打招呼（默认） 1=本次打招呼 2=他端已沟通 |
| `score` | INTEGER | AI 匹配分（-1=未分析，0–100=已分析） |
| `should_apply` | INTEGER | -1=未分析 0=不推荐 1=推荐投递 |
| `key_matches` | TEXT | 匹配点（JSON 数组） |
| `missing_skills` | TEXT | 缺失技能（JSON 数组） |
| `skip_reason` | TEXT | 不推荐时的原因 |
| `resume_file` | TEXT | 定制简历文件名（暂未实现，默认空） |
| `created_at` | TEXT | 首次写入时间 |
| `updated_at` | TEXT | 最后更新时间 |

---

---

## chat/handler.py — IM 聊天自动化（原 chat_handler.py）

### 启动方式

```
双击 start_chrome.bat              # port 9222
  → 手动登录 BOSS直聘，导航到 /web/geek/chat
  → python src/chat/handler.py          # 开始处理聊天
```

### 配置参数（config.py）

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `CDP_CHAT_PORT` | `9222` | 聊天模块 Chrome 调试端口（与 scanner 共用同一 Chrome 实例） |
| `DIRECT_MODE` | `False` | `True`：直接处理当前右侧可见会话一次后退出（测试用）；`False`：正常轮询左侧列表 |
| `POLL_LIMIT` | `5` | 单次运行最多处理会话数 |
| `REPLY_ENABLED` | `False` | `True`：发送消息；`False`：只做卡片同意和发简历，不产生新消息，不调用 AI API |
| `SEND_ENABLED` | `False` | `True`：点击发送按钮；`False`：只打入输入框不发送 |
| `CONSERVATIVE_CHAT` | `True` | `True`：不回复 boss 消息，仅发简历后或场景 A 下发固定自我介绍；`False`：AI 生成回复 |
| `SELF_PROMO_TEXT` | (固定文案) | 自我介绍固定文案，所有场景统一使用，不走 AI |
| `DISCLAIMER` | (免责声明) | 所有消息末尾自动附加 |
| `GENERATE_TAILORED_RESUME` | `True` | `True`：按 JD 生成定制简历上传发送；`False`：发送默认简历 |

**轮询策略（`DIRECT_MODE=False`）：**

维护 `processed_eids` 集合（已处理的 `encryptJobId`），`while processed < POLL_LIMIT`：

- `get_all_sessions` → 过滤已处理 → for 循环按 DOM 顺序依次处理所有候选
- 每个候选通过 `scroll_into_view_and_click` + `encryptJobId` 精确定位（自然触发虚拟列表懒加载）
- for 循环结束后 `continue` 重读列表，捡起新渲染的会话
- 重读后无新候选 → `break`，程序退出

**不考虑未读消息数量**，不考虑手动翻页。

> **encryptJobId 来源**：`_JS_GET_SESSIONS` 从 `li.__vue__.$props.source.encryptJobId` 读取，不依赖 href 解析。读取失败（空字符串）视为致命错误，`sys.exit(1)` 直接终止。

**左侧会话卡片点击（防视口外失效 + 防列表重排错位）：**
点击前先调用 `small_human_scroll(tab, lo=100, hi=350)` 模拟人类操作。卡片定位使用 `encryptJobId` 精确匹配（从 `li.__vue__.$props.source.encryptJobId` 读取，不依赖列表索引），通过 `scroll_into_view_and_click` 执行 `scrollIntoView` → 取最新坐标 → 点击，防止列表重排后用旧坐标点到错误会话。点击后轮询 `window.chat.communicating.encryptJobId`，确认右侧已切换到目标会话后再调用 `process_session`。

**`REPLY_ENABLED=False` 时的行为：**
- `handle_interactive_cards`（卡片同意）和 `execute_resume_action`（发简历）正常执行
- 所有 `_type_and_log` 调用只打印日志，不操作输入框也不点击发送
- 所有消息发送（含固定自我介绍和 AI 回复）跳过，不发出 API 请求

**`SEND_ENABLED=False` 时的行为：**
- 文字正常打入输入框，等待延时后 return，不点击发送按钮
- 消息留在输入框供人工确认后手动发送

### 已验证的 IM 页面选择器（2026-06，/web/geek/chat）

| 元素 | 选择器 |
|------|--------|
| 会话列表滚动容器 | `.user-list-content` |
| 单个会话卡片 | `.user-list-content > ul:nth-child(2) > li` |
| 会话卡片 - 姓名 | `.name-text` |
| 会话卡片 - 公司 | `.name-box > span:nth-child(2)` |
| 会话卡片 - 职位/身份 | `.name-box > span:last-child` |
| 会话卡片 - 时间 | `.time` |
| 会话卡片 - 消息预览 | `.last-msg-text` |
| 会话卡片 - 未读角标 | `.notice-badge` |
| 聊天消息容器 | `.chat-content` |
| 单条消息 | `.message-item` |
| 我方消息 | `item-myself` class（`isSelf=True`） |
| 对方消息 | `item-friend` class，无 `articles-center` |
| 系统通知 | `item-system` class，或含 `articles-center`（PK卡片） |
| 交互卡片（简历/微信请求） | `item-friend` + `.message-card-wrap` + `.card-btn`（未 disabled） |
| 同意/拒绝按钮 | `span.card-btn`（text='同意'/'拒绝'，`disabled=False`） |
| 文字输入框 | `div.chat-input[contenteditable='true']` |
| 发送按钮 | `button.btn-send`（空输入时带 `disabled` class） |
| 发简历按钮 | `.toolbar-btn-content`（text='发简历'） |
| 换电话 | `div.btn-contact` |
| 换微信 | `div.btn-weixin` |
| 简历选择弹窗容器 | `.boss-popup__wrapper`（z-index=2014） |
| 简历列表项 | `span.resume-name`（含「袁柯」）|
| 简历弹窗确认按钮 | `.btn-confirm`（text='发送'） |
| 未读总数（顶导） | `span.nav-chat-num` |
| 当前会话信息 | `window.chat.communicating`（`_JS_CHAT_INFO` 实际提取 encryptJobId/companyName/title/name/bothTalked/jobName/locationName；薪资字段已不再从此处提取，改与 JD 同链路获取，见上文「薪资字段（chat 聊天页）」） |

#### 「查看职位」详情页选择器（job_detail_fetch.py，新标签页 `/job_detail/<id>`）

| 元素 | 选择器 |
|------|--------|
| 「查看职位」按钮 | `.position-content .right-content span`（取含"查看职位"文字的项） |
| 职位名称 | `.job-name`（备选 `.job-banner .name h1`） |
| 薪资（明文，无需解码） | `.job-banner .name .salary`（备选 `.salary`） |
| 城市 | `.job-banner .text-desc.text-city`（备选 `.text-city`） |
| JD 正文 | `.job-sec-text` |
| 招聘者信息 | `.job-boss-info`（结构同 scanner JD 面板，见上文） |

#### 定制简历附件管理选择器（resume_attachment.py，简历管理弹层/页面）

| 元素 | 选择器 |
|------|--------|
| 附件列表项 | `.annex-item` |
| 附件操作区 / 删除按钮 | `.annex-item-operate` / `.annex-operate-delete` |
| 简历附件管理入口 | `.resume-attachment a.sider-title-operate` |
| 简历类型标题 | `.resume-type-title` |
| 上传/发送确认弹窗（按钮变体） | `span.btn-v2` / `.btn-sure-v2` |
| 简历确认气泡（popover 变体） | `.panel-resume.sentence-popover`（见 `resume_dialog._handle_resume_confirm_popover`） |

### 模块架构（chat_handler 拆分后）

`session_actions.py` 原本是 1232 行的大文件，已按主题进一步拆分为多个子模块，
现在只保留 `execute_session_actions` 编排逻辑，其余按职责分散到同目录下的专用文件中：

| 文件 | 职责 |
|------|------|
| `src/chat/handler.py` | 主循环、CDP 连接、会话列表轮询 |
| `src/shared/cdp_utils.py` | `evaluate` / `cdp_click` / `cdp_wheel` / `random_delay` / `small_human_scroll` / `is_browser_alive` / `read_messages` / `scroll_into_view_and_click`（防视口外点击失效的通用助手，定位元素→`scrollIntoView`→取最新坐标→点击，handler 左侧会话卡片点击与 resume_attachment `click_session_card` 复用） |
| `src/shared/database.py` | SQLite CRUD（jobs + chats 两张表） |
| `src/chat/session_processor.py` | `process_session`：阶段一读取分析 + 阶段二写库 |
| `src/chat/session_actions.py` | `execute_session_actions`：会话操作主入口/编排（场景 A/B/C 分支），重新导出 `fetch_job_detail_via_view_job` 供 `session_processor` 使用 |
| `src/chat/ai.py` | `call_ai`、`_SYS_PROMPT`、`_fmt_history`：AI 回复生成（自我介绍已改用固定文案 `SELF_PROMO_TEXT`） |
| `src/chat/messaging.py` | `clear_and_type`、`click_send`、`type_and_log`：输入框打字/发送与统一日志输出 |
| `src/chat/resume_dialog.py` | `handle_resume_dialog`、`click_resume_btn`、`_handle_resume_confirm_popover`：默认简历的发送弹窗/确认气泡处理 |
| `src/chat/resume_attachment.py` | `execute_resume_action`（简历操作分发，按 `GENERATE_TAILORED_RESUME` 决定走定制简历还是默认简历）、`upload_resume_attachment`/`delete_resume_attachment`/`click_session_card`/`_FileChooserCatcher` 等定制简历附件管理全套流程 |
| `src/chat/interactive_cards.py` | `handle_interactive_cards`、`_read_agree_cards`：微信交换/沟通意向等非简历交互卡片的「同意」批量处理 |
| `src/chat/job_detail_fetch.py` | `fetch_job_detail_via_view_job`：「查看职位」详情页抓取（新标签页打开→提取→关闭） |

**模块间依赖（无环）：**
```
shared/cdp_utils ←─┬─ chat/ai
                   ├─ chat/messaging
                   ├─ chat/resume_dialog ←── chat/resume_attachment
                   ├─ chat/interactive_cards
                   ├─ chat/job_detail_fetch
                   └─ chat/resume_attachment, ai, messaging, resume_dialog,
                      interactive_cards, job_detail_fetch
                            ↑
                      chat/session_actions ←── chat/session_processor ←── chat/handler
shared/database  ←──────────────────────────── chat/session_processor
```

`resume_attachment.execute_resume_action` 在 `GENERATE_TAILORED_RESUME=True` 且有 JD 时还会按需
`from chat.resume_tailor import generate_tailored_resume` / `from chat.session_processor import load_resume`
（函数内延迟导入，避免与 `session_processor` 形成模块级循环依赖）。

### 消息类型分类规则

```javascript
// shared/cdp_utils.py _JS_READ_MESSAGES 中的分类逻辑
isSelf   = cls.includes('item-myself')
isSystem = cls.includes('item-system') || !!querySelector('.articles-center')
isCard   = !!querySelector('.message-card-wrap') || isSystem
isInteractiveCard = hasCardWrap && cardBtns.length > 0
```

- `from="me"`：`isSelf=True`
- `from="system"`：`isSystem=True`（系统通知 + PK卡片）
- `from="boss"`：其余（boss 文字消息 + boss 卡片）

`my_texts`：`isSelf=True AND isCard=False` 的消息
`boss_texts`：`isSelf=False AND isSystem=False AND isCard=False` 的消息

`last_is_boss`：从末尾反向找第一条非系统、非卡片、有文字的消息，判断是否来自 boss。替代原有所有快照变量（`_snap` / `last_is_boss_snap` 等）。

### 卡片类型识别（基于 DOM 元素）

通过 icon 元素的次级 class 判断，不依赖卡片文字：

| icon 选择器 | 次级 class | 卡片类型 | 点击「同意」后行为 |
|---|---|---|---|
| `span.dialog-icon` | `resume` | 简历请求 | 弹出简历选择弹窗 |
| `span.dialog-icon` | `weixin` | 微信交换请求 | 系统自动发送新消息 |
| `span.dialog-icon` | `note` | 沟通意向 | 系统自动发送新消息 |
| `span.concat-icon` | `wechat` | 微信号展示卡 | 无同意按钮，不操作 |

实现在 `chat/session_actions.py` 的 `_JS_FIND_AGREE_CARDS` / `_read_agree_cards` 和 `handle_interactive_cards`。

### 会话回复逻辑（process_session）

执行顺序：**阶段一读取分析（无副作用）→ 阶段二写库（一次）→ 阶段三执行操作**

#### 阶段一：读取与分析

1. 读取会话基本信息（`get_current_chat_info`），取出 `encryptJobId`
   **`encryptJobId` 读取失败即视为致命错误**（正常情况下必定存在，读不到说明选择器失效）→
   记录日志后 `sys.exit(1)` 直接终止程序，便于排查，不再继续运行
2. 查岗位表（`get_job_by_encrypt_id`），命中则取出 `jd`，否则 `jd=""`
3. **一次性读取消息**（`read_messages`），此后整个函数不再调用；消息为空则直接 return
4. 分类计算：`my_texts` / `boss_texts` / `initiator` / `last_is_boss`
5. **简历状态检测**（纯读取）：

```
db_resume_sent=True       → resume_already_sent=True
系统消息含「简历」字样     → resume_already_sent=True
以上均不满足              → resume_already_sent=False
```

6. 加载简历（`load_resume`）
7. 未匹配到岗位记录（`job_row` 为空）时：通过「查看职位」补录完整岗位信息
   （`fetch_job_detail_via_view_job` 点击右侧职位面板「查看职位」→ 在新打开的
   `job_detail` 标签页提取岗位名称/地点/薪资/公司名称/JD/招聘者姓名/招聘者title →
   关闭标签页并切回聊天页 → `save_job_from_view_detail` 写入 jobs 表，`source='chat'`）。
   成功后立即用获取到的 `jd` 更新本轮上下文，无需等待下一轮轮询命中 `job_row`。
   该详情页薪资为明文（与扫描页 `.job-salary` 的 kanzhun-mix 混淆不同），无需解码；
   若提取失败（按钮未找到/新标签页超时/JD 为空）则不写库，`jd` 保持为空继续。

#### 阶段二：写库（操作前，仅一次）

`encrypt_job_id` 已在阶段一保证非空，调用一次 `upsert_chat`，写入：
- `chat_history`（当前消息快照）、基础字段、`resume_sent`

#### 阶段三：执行操作（委托 execute_session_actions）

自我介绍使用 `config.SELF_PROMO_TEXT` 固定文案，不走 AI。`call_ai` 仅生成回复，返回字符串（空字符串表示失败）。

**Step 1（无条件）：`handle_interactive_cards(tab)`**

循环处理非简历交互卡片「同意」按钮，简历请求卡留给 Step 2。

**Step 2：按 `my_texts` / `boss_texts` 是否为空分三种场景**

```
场景C：my_texts 为空（Boss 主动发起，我方无消息）
  → execute_resume_action（失败则 return）
  → _send_self_promo("自我介绍")  # 固定文案，检查 sent_self_promo 去重

场景A：boss_texts 为空（我方主动发起，Boss 尚未回复）
  → _send_self_promo("自我推荐")  # 固定文案，检查 sent_self_promo 去重

场景B：双方均有消息
  if not resume_already_sent → execute_resume_action（失败则 return）
  need_self_promo = (resume_sent_now == 1 AND not self_promo_already_sent)
  need_reply      = False if CONSERVATIVE_CHAT else last_is_boss
  若 need_self_promo → _send_self_promo（固定文案）
  若 need_reply AND not CONSERVATIVE_CHAT → call_ai(jd, salary) 生成回复
```

**去重机制**：`sent_self_promo` 字段（chats 表，`INTEGER DEFAULT 0`），DB 读取 + DOM 检测（匹配 `SELF_PROMO_TEXT` 前缀）双重判断是否已发送，`upsert_chat` 用 `MAX` 单调递增。

`execute_resume_action(tab, company, jd, target)`（`src/chat/resume_attachment.py`）按 `GENERATE_TAILORED_RESUME` 分发：

```
GENERATE_TAILORED_RESUME=True 且 jd 非空：
  生成定制简历 generate_tailored_resume(company, jd, load_resume()) → pdf_path
  成功则依次：
    upload_resume_attachment(tab, pdf_path, target)   # 上传附件（点击「简历」→ 等待跳转到 /web/geek/resume，最多重试 5 次×2-3s）
    → random_delay(2, 3)                              # 等发送弹窗关闭
    → click_resume_btn(tab, resume_name_match=tailored_match)  # tailored_match="袁柯_"，点「发简历」触发弹窗 + 选中刚上传的定制简历并发送
    → delete_resume_attachment(tab, tailored_match, target)     # 删除附件（点击「简历」→ 等待跳转，最多重试 5 次×2-3s）
  任一环节失败/异常 → return False，上层跳过该对话（不发消息、不切默认简历）
否则（GENERATE_TAILORED_RESUME=False 或 jd 为空）：
  click_resume_btn(tab)  # 默认 resume_name_match="袁柯"，点工具栏「发简历」+ 处理弹窗
```

**模糊匹配字段选取**（`resume_name_match`，子串 `includes` 匹配，避免互相误命中）：
- 默认简历匹配 `"袁柯.pdf"`（完整文件名子串，只命中固定文件 `袁柯.pdf`，不含下划线故不会误命中 `袁柯_xxx.pdf`）
- 定制简历命名为 `袁柯_{公司名}_{时间戳}.pdf`（见 `resume_tailor.py`）→ 匹配串用稳定前缀 `"袁柯_"`
  而非完整 stem：完整 stem 含公司名+时间戳，平台展示时可能截断/转义导致子串匹配失败；
  且 `"袁柯_"` 不含 `.pdf` 后缀，与默认匹配串 `"袁柯.pdf"` 互相不误判

#### 简历弹窗处理（handle_resume_dialog，`src/chat/resume_dialog.py`）

点击「发简历」后会出现两种互斥提示之一（取决于附件数量，调用方无需预判，函数内部轮询检测先出现的一种）：

```
A. 附件数 > 1 → 排他模态框 .boss-popup__wrapper：
   1. 找 span.resume-name 中含 resume_name_match 的项 → cdp_click 选中（找不到返回 False）
   2. 等待 0.8s（选中状态渲染）
   3. 找 .btn-confirm（text='发送'，找不到则在弹窗内按文字兜底查找）→ cdp_click 确认
   4. 等待 1.5-2.5s → 返回 True

B. 附件数 == 1 → 非模态确认气泡 .panel-resume.sentence-popover（标题"确定向 Boss 发送简历吗？"）：
   无需选择简历项，直接找 span.btn-v2 中 text='确定' 的按钮点击确认 → 返回 True

轮询等待两种弹层之一出现（最多 5 秒，超时返回 False）
```

`click_resume_btn(tab, resume_name_match="袁柯")`：找 `.toolbar-btn-content` 中文字含「发简历」的按钮 →
`cdp_click` → `handle_resume_dialog(resume_name_match=...)`。


### chats 表字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER | 主键，自增 |
| `encrypt_job_id` | TEXT UNIQUE | 对应 `jobs.job_id`（encryptJobId） |
| `jobs_db_id` | INTEGER | 对应 `jobs.id` |
| `boss_name` | TEXT | Boss 姓名（来自 `window.chat.communicating.name`） |
| `company` | TEXT | 公司名称 |
| `boss_title` | TEXT | Boss 职位（招聘者/HR等） |
| `initiator` | TEXT | 发起方：`"me"`=我主动 / `"boss"`=对方主动 |
| `chat_history` | TEXT | JSON 数组，每条含 from/text/time/status/isCard |
| `resume_sent` | INTEGER | 0=未发 1=已发（任何方式） |
| `sent_self_promo` | INTEGER | 0=未发 1=已发自我介绍 |
| `created_at` | TEXT | 首次写入时间 |
| `updated_at` | TEXT | 最后更新时间 |

> 表中物理上还残留 `tendency_score` / `ai_reasoning`（聊天倾向评分功能移除后的遗留列）以及
> `salary_desc` / `salary_low` / `salary_high`（薪资改走 `jobs.salary` 同链路后的遗留列），
> SQLite 不支持简单地删列，这些字段已不再被任何代码读写，可忽略。

---

## 注意事项

- **不要以管理员身份运行** `start_chrome.bat`，否则 Chrome 附加 `--no-sandbox` 导致行为异常
- **每次重启 Chrome** 需重新手动登录，登录状态保存在 `browser_data/`
- **随机延时**：卡片点击后等 1.5–2.5s，卡片间隔 1–3s，避免触发频率限制
- **DB 去重（点击前查库）**：拿到 `encryptJobId` 后**先于点击**精确匹配 `jobs.job_id`。命中后按三级条件过滤：非目标城市→跳过；已打招呼(`greeted≠0`)→跳过；明确不推荐(`should_apply=0`)→跳过。均不满足则重新处理（更新已有记录）。未命中才走完整的新岗位入库流程
- **城市字段为空时不过滤**：避免因 DOM 变化读不到城市而误跳过北京岗位
- **BOSS 投递限制**：对方未回复时无法投递简历，这是平台规则
