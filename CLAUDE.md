# Job Hunter — 项目说明

## 项目概述

基于 Python + Chrome CDP + Claude API + SQLite 的 BOSS直聘自动化求职脚本。使用 Chrome 调试模式（远程调试端口 9222/9223）+ pychrome 控制浏览器，绕过 BOSS直聘反爬检测，自动完成岗位扫描、AI 匹配分析、发起沟通、聊天回复全流程。

两个模块**同时运行**，各用独立 Chrome 实例：
- `job_scanner.py`：扫描职位列表，AI 匹配，发起沟通 → Chrome A（port 9222）
- `chat_handler.py`：处理 IM 聊天，发简历，AI 回复 → Chrome B（port 9223）

---

## 技术方案

### 浏览器控制

**不使用 Playwright**，原因：
- `connect_over_cdp` 会向页面注入运行时，触发 BOSS 检测并跳转到 `about:blank`
- Playwright Chromium 的 TLS 指纹、Canvas 渲染特征与真实 Chrome 不同，页面加载即被识别

**正确方案**：用 `start_chrome_job.bat` 启动系统 Chrome（`--remote-debugging-port=9222`），用户手动登录后，脚本通过 `pychrome` + CDP 连接操作。

```
双击 start_chrome_job.bat
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
- **薪资字段（chat 聊天页）**：与列表页不同，`window.chat.communicating.salaryDesc`（明文字符串，如 "25-50K·14薪"）及 `lowSalary`/`highSalary`（原始整数）**未经混淆**，可直接读取入库（见 `chats` 表 `salary_desc`/`salary_low`/`salary_high` 字段）
- **页面结构**：BOSS直聘为 React SPA，CSS 类名带版本哈希，选择器可能随版本失效，失效时用 Chrome DevTools 手动定位后更新 `src/scanner/scanner.py` 和 `src/chat/session_actions.py` 中对应的选择器常量

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
├── start_chrome_job.bat         # Chrome A（port 9222）scanner 用
├── start_chrome_chat.bat        # Chrome B（port 9223）chat 用
├── requirements.txt             # 依赖列表
├── src/
│   ├── config.py                # 全局配置（BASE_DIR 指向 job-hunter/）
│   ├── confidential.py          # 敏感配置：API Key 等（.gitignore，不上传）
│   ├── scanner/                 # job_scanner 全部逻辑
│   │   ├── __init__.py
│   │   ├── scanner.py           # 入口（原 job_scanner.py）
│   │   ├── analyzer.py          # AI 匹配分析（仅 scanner 使用）
│   │   └── salary_decoder.py    # .job-salary 薪资解码（canvas 像素比对，仅 scanner 使用）
│   ├── chat/                    # chat_handler 全部逻辑
│   │   ├── __init__.py
│   │   ├── handler.py           # 入口（原 chat_handler.py）
│   │   ├── session_processor.py # 会话读取、分析、写库
│   │   └── session_actions.py   # 会话执行操作（AI、发消息、简历、卡片）
│   └── shared/                  # 两侧共用基础模块
│       ├── __init__.py
│       ├── database.py          # SQLite CRUD（jobs + chats 两张表）
│       ├── cdp_utils.py         # CDP 底层工具函数：evaluate/cdp_click/cdp_wheel/random_delay/small_human_scroll/is_browser_alive/read_messages
│       └── logger.py            # 双路日志：控制台(INFO) + 文件(DEBUG，5MB×3)
├── resume/
│   ├── 袁柯.pdf                 # 原始简历
│   └── 袁柯.txt                 # 简历文本缓存（首次运行自动生成）
├── records/
│   └── jobs.db                  # SQLite 数据库（jobs + chats 两张表）
├── logs/
│   └── app.log                  # 运行日志
├── browser_data/                # Chrome A 用户数据（scanner 登录状态）
└── browser_data_chat/           # Chrome B 用户数据（chat 登录状态）
```

**sys.path 规则**：`src/` 各子包文件头部均有 `sys.path.insert(0, str(Path(__file__).parent.parent))`，将 `src/` 加入路径，跨包导入使用绝对路径（`from shared.database import ...`、`from chat.session_processor import ...`）。

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

---

## 运行参数（scanner.py 顶部常量 + config.py）

| 常量 | 位置 | 默认值 | 说明 |
|------|------|--------|------|
| `TARGET_CITY` | scanner.py | `"北京"` | 目标城市，非此城市只入库不解析 |
| `MAX_SCAN` | scanner.py | `100` | 单次运行最多扫描岗位数，达到后停止 |
| `SCAN_API_ENABLED` | config.py | `False` | `True`=调用 AI API 分析匹配度；`False`=跳过，score=0 |
| `SCAN_GREET_ENABLED` | config.py | `False` | `True`=点击「立即沟通」并处理弹窗；`False`=只扫描不打招呼 |
| `SCORE_THRESHOLD` | config.py | `70` | AI 匹配分阈值，≥70 才推荐投递 |
| `SCROLL_DELTA` | scanner.py | `2000` | 每次翻页滚动像素 |
| `SCROLL_WAIT` | scanner.py | `2.5` | 滚动后等待新卡片加载的秒数 |
| `STALE_LIMIT` | scanner.py | `2` | 连续无新卡片次数达到此值则判定末页停止 |

---

## 主流程逻辑（job_scanner.py）

### 1. 启动

初始化数据库（`init_db()`），连接 CDP，找到 BOSS直聘标签页。

### 2. 主循环：无限滚动翻页

外层 `while True` 负责翻页，内层 `for card in new_cards` 处理每张卡片。用 `processed_idxs` 集合记录已处理的卡片 idx，防止重复处理。

**翻页逻辑**：当前所有卡片都已处理后，同时发送 CDP `mouseWheel` 事件和 JS `window.scrollTo(bottom)` 触发加载，等待 `SCROLL_WAIT` 秒。连续 `STALE_LIMIT` 次滚动后卡片数不再增加，判定到达末页，退出循环。

**停止条件**：每次外层循环开头先检测浏览器是否存活（`is_browser_alive`），浏览器关闭时立即退出；已处理卡片数达到 `MAX_SCAN`（默认 100）时同样退出。打招呼数量不再作为停止条件。

### 3. 单张卡片处理流程

任何步骤抛出异常则记录 `errors` 并跳过，卡片间随机等待 1–3 秒。

#### 步骤一：点击卡片，读取 JD 和城市

点击前先调用 `small_human_scroll(tab)` 模拟人类浏览行为（随机小幅滚动 80–280px）。获取卡片坐标时先执行 `scrollIntoView({block:'center', behavior:'instant'})`，确保卡片在视口内再取 `getBoundingClientRect()` 坐标，避免视口外点击失效导致 JD 错配。CDP 鼠标点击卡片中心坐标，等待 1.5–2.5 秒。

**面板公司校验（防错配）**：点击后从 JD 面板读取公司名（`.job-detail-header .company-info .name`），与卡片列表中的公司名比对，不匹配则跳过该卡片，避免处理旧面板内容写入错误记录。

**JD 提取（DOM 遍历，方案A）**：取 `.job-detail-body`，以 `h3.title` 为锚点（页面固定的结构标题，属于噪音），收集其后所有兄弟节点文本，遇到 `boss-info / detail-op / work-addr` 等 class 或「去App / 工作地址」等文字则停止。不依赖关键词匹配，天然保留正文首行（即使首行是「职位描述」）和「【岗位职责】」等带括号格式。

**城市提取**：读取 `.job-detail-header .tag-list li:first-child a` 的文字（JD header 的标签列第一项始终是城市）。

#### 步骤二：城市过滤

```
city 非空 且 city ≠ TARGET_CITY（"北京"）：
  → DB 去重（position + company + jd 三字段精确匹配）
      命中 → 跳过，不重复入库
      未命中 → save_job（只存基础字段，analyzed=0, greeted=0）
  → continue（跳过 AI 分析和沟通）

city == TARGET_CITY 或 city 为空（无法读取时不过滤）：
  → 继续后续步骤
```

#### 步骤三：DB 去重

JD 非空时，按（职位名 + 公司名 + JD 全文）三字段精确查询：
- **命中** → `continue` 到下一张卡片
- **未命中** → 继续 AI 分析

#### 步骤四：AI 匹配分析（受 `SCAN_API_ENABLED` 控制）

仅 `SCAN_API_ENABLED=True` 时执行，否则跳过（score=0, should_apply=False）。调用 `analyzer.analyze_job(company, name, jd)`：
1. 读取简历（优先 `resume/袁柯.txt` 缓存，否则解析 PDF 并写缓存）
2. 调用 Claude API，System 提示要求只输出 JSON
3. 剥除响应中可能的 markdown 代码块（` ```json ``` `）
4. 解析失败或 API 异常时返回 `score=0, should_apply=False`
5. `should_apply = score >= SCORE_THRESHOLD`

#### 步骤五：立即沟通（受 `SCAN_GREET_ENABLED` 控制）

条件：`should_apply = True` 且 `SCAN_GREET_ENABLED = True`。`SCAN_GREET_ENABLED=False` 时跳过此步骤（只记录推荐，不点击沟通按钮）。记录 `url_before`，CDP 点击 `.op-btn-chat`，等待 1–1.5 秒，检测结果：

| 检测结果 | 含义 | 处理 | greet_status |
|---------|------|------|---|
| `.cancel-btn` 出现 | 首次沟通，弹窗出现 | 点击「留在此页」，等 0.5–1s | 1 |
| URL 发生变化 | 他端已沟通，直接跳转会话列表 | `Page.navigate` 回 `url_before` → 等 2.5–3.5s → 点击「数据分析师」求职期望 tab → 等 2–2.5s | 2 |
| 两者都没有 | 异常，点击无响应 | 跳过 | 0 |

**回退后恢复**：`Page.navigate` 回原 URL 后，页面默认停在「推荐」tab，需点击 `.expect-item`（含"数据分析师"文字）切回目标搜索结果，等待列表刷新后继续扫描。

#### 步骤六：写入数据库

条件：JD 非空。

`analyzed` 取值：
- `greet_status = 2` → `analyzed = 2`（跳过 AI，因他端已沟通）
- `analysis` 非空 → `analyzed = 1`
- 否则 → `analyzed = 0`

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
| `city` | TEXT | 城市（从 JD header 提取） |
| `analyzed` | INTEGER | 0=未解析 1=本次API解析 2=跳过(他端已沟通) |
| `score` | INTEGER | AI 匹配分（0–100） |
| `should_apply` | INTEGER | 是否推荐投递（0/1） |
| `key_matches` | TEXT | 匹配点（JSON 数组） |
| `missing_skills` | TEXT | 缺失技能（JSON 数组） |
| `skip_reason` | TEXT | 不推荐时的原因 |
| `greeted` | INTEGER | 0=未打招呼 1=本次打招呼 2=他端已打招呼 |
| `resume_file` | TEXT | 定制简历文件名（暂未实现，默认空） |
| `created_at` | TEXT | 首次写入时间 |
| `updated_at` | TEXT | 最后更新时间 |

---

---

## chat_handler.py — IM 聊天自动化

### 启动方式

```
双击 start_chrome_chat.bat              # Chrome B，port 9223
  → 手动登录 BOSS直聘，导航到 /web/geek/chat
  → python src/chat/handler.py          # 开始处理聊天
```

### 配置参数（config.py）

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `CDP_CHAT_PORT` | `9223` | 聊天模块 Chrome 调试端口 |
| `CONTINUOUS_POLL` | `True` | `True`：持续轮询左侧会话列表；`False`：处理当前右侧可见会话一次后退出，且不点击左侧卡片 |
| `POLL_LIMIT` | `1` | 单轮最多处理会话数（生产时改 50，仅 `CONTINUOUS_POLL=True` 时生效） |
| `CHAT_MAX_AGE_DAYS` | `100` | 超过此天数的会话跳过并重头轮询 |
| `REPLY_ENABLED` | `True` | `True`：正常发送固定话术和 AI 回复；`False`：只做卡片同意和发简历，不产生新消息，不调用 AI API |
| `SEND_ENABLED` | `True` | `True`：点击发送按钮；`False`：只打入输入框，不点击发送（`REPLY_ENABLED=False` 时此开关无效） |
| `FIXED_SELF_INTRO` | 固定文字 | 无JD 时 boss 主动发起/双方有消息 的自我介绍 |
| `FIXED_FOLLOWUP` | 固定文字 | 无JD 时我方主动但 boss 未回复 的跟进话术 |
| `DISCLAIMER` | `""` | 所有消息末尾附加的免责声明（暂时置空） |

**`CONTINUOUS_POLL=False` 时的行为：**
- 不遍历左侧会话列表，直接对当前右侧可见会话调用一次 `process_session(session_info=None)`，结束后退出
- 不点击左侧会话卡片，右侧信息全部从 `window.chat.communicating` 和 DOM 读取，不存在左右侧数据错位问题

**左侧会话卡片点击（防视口外失效）：**
点击前先调用 `small_human_scroll(tab, lo=100, hi=350)` 模拟人类操作，随后执行 `scrollIntoView({block:'center', behavior:'instant'})` 确保卡片进入视口，再取最新坐标点击，防止卡片超出视口时点击失效导致右侧消息框错位。

**`REPLY_ENABLED=False` 时的行为：**
- `handle_interactive_cards`（卡片同意）和 `execute_resume_action`（发简历）正常执行
- 所有 `_type_and_log` 调用只打印日志，不操作输入框也不点击发送
- 所有 `call_ai` / `call_ai_self_promo` 调用直接跳过，不发出 API 请求

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
| 当前会话信息 | `window.chat.communicating`（含 name/companyName/encryptJobId/jobName/salaryDesc/lowSalary/highSalary 等） |

### 模块架构（chat_handler 拆分后）

| 文件 | 职责 |
|------|------|
| `src/chat/handler.py` | 主循环、CDP 连接、会话列表轮询 |
| `src/shared/cdp_utils.py` | `evaluate` / `cdp_click` / `cdp_wheel` / `random_delay` / `small_human_scroll` / `is_browser_alive` / `read_messages` |
| `src/shared/database.py` | SQLite CRUD（jobs + chats 两张表） |
| `src/chat/session_processor.py` | `process_session`：阶段一读取分析 + 阶段二写库 |
| `src/chat/session_actions.py` | `execute_session_actions`：所有执行操作（AI、发消息、简历、卡片） |

**模块间依赖（无环）：**
```
shared/cdp_utils  ←  chat/session_actions  ←  chat/session_processor  ←  chat/handler
shared/database   ←  chat/session_processor
```

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

实现在 `modules/session_actions.py` 的 `_JS_FIND_AGREE_CARDS` 和 `handle_interactive_cards`。

### 会话回复逻辑（process_session）

执行顺序：**阶段一读取分析（无副作用）→ 阶段二写库（一次）→ 阶段三执行操作**

#### 阶段一：读取与分析

1. 读取会话基本信息（`get_current_chat_info`）
2. 时间检查（`is_session_too_old`），超期直接 return
3. 查岗位表（`get_job_by_encrypt_id`）
4. **一次性读取消息**（`read_messages`），此后整个函数不再调用
5. 分类计算：`my_texts` / `boss_texts` / `has_jd` / `initiator` / `last_is_boss`
6. **简历状态检测**（纯读取）：

```
db_resume_sent=True           → resume_already_sent=True
boss_texts 为空               → resume_already_sent=False（无需发简历）
my_texts 为空                 → resume_already_sent=False（平台限制，boss不能发请求卡片）
系统消息含「简历」字样         → resume_already_sent=True
以上均不满足                  → resume_already_sent=False
```

7. 加载简历（`load_resume`）
8. 无JD 且 job_row 为空时：`save_job_from_chat` 补录 jobs 表

#### 阶段二：写库（操作前，仅一次）

若 `encrypt_job_id` 非空，调用一次 `upsert_chat`，写入：
- `chat_history`（当前消息快照）、基础字段、`resume_sent`
- **不传** `tendency_score` / `ai_reasoning`（SQL 保留库中已有值）

#### 阶段三：执行操作（委托 execute_session_actions）

**Step 1（无条件）：`handle_interactive_cards(tab)`**

处理所有可点击「同意」的交互卡片（共 x 张）：
```
有简历请求卡：
  循环 x-1 次 → 每次重新读坐标，点击一张非简历卡（等待系统消息 1.5-2.5s）
  最后重新读简历卡坐标 → cdp_click 同意 → handle_resume_dialog → resume_sent_now=1

无简历请求卡：
  循环 x 次 → 每次重新读坐标，点击非简历卡
  resume_sent_now=0
```

`resume_sent_now`（本轮是否已发简历）由 Step 1 结果初始化，Step 2 分支可继续累加。

**Step 2：按分支执行**

```
has_jd=False：
  my_texts 为空（无JD-C）：
    if not resume_sent_now → execute_resume_action(tab)  # click_resume_btn
    → _type_and_log(FIXED_SELF_INTRO)

  boss_texts 为空（无JD-A）：
    → _type_and_log(FIXED_FOLLOWUP)

  双方均有消息（无JD-B）：
    if not (resume_already_sent or resume_sent_now) → execute_resume_action(tab)
    need_self_promo = resume_sent_now==1
    need_reply      = last_is_boss
    → call_ai(jd="") → 先发 self_promo，延迟 2-3s，再发 reply

has_jd=True：
  boss_texts 为空（模式A）：
    → call_ai_self_promo → _type_and_log(promo)

  双方均有消息（模式B）：
    if not (resume_already_sent or resume_sent_now) → execute_resume_action(tab)
    need_self_promo = resume_sent_now==1
    need_reply      = last_is_boss
    → call_ai(jd) → 先发 self_promo，延迟 2-3s，再发 reply
```

`execute_resume_action(tab)` 直接调用 `click_resume_btn(tab)`（主动点工具栏「发简历」按钮）。

#### AI Prompt 切换（call_ai 内部自动判断）

`call_ai(jd="")` 时自动切换到 `_SYS_PROMPT_NO_JD`，避免模型因 JD 上下文为空而返回自然语言：

| | `_SYS_PROMPT`（有JD） | `_SYS_PROMPT_NO_JD`（无JD） |
|-|----------------------|----------------------------|
| 触发条件 | `jd` 非空 | `jd` 为空 |
| JD 段 | `【职位JD】公司：...\n{jd}` | `【说明】无完整JD，仅供参考\n公司：...` |
| 自我推荐要求 | 突出与岗位相关的经历 | 突出简历中最有竞争力的经历 |
| JSON 格式 | 相同 | 相同 |

#### 简历弹窗处理（handle_resume_dialog）

```
1. 轮询等待 .boss-popup__wrapper 出现（最多 5 秒）
2. 找 span.resume-name 中含「袁柯」的项 → cdp_click 选中
3. 等待 0.8s（选中状态渲染）
4. 找 .btn-confirm（text='发送'）→ cdp_click 确认
5. 等待 1.5-2.5s
→ DEBUG_MODE：步骤4 改为点×关闭，返回 False（不触发 resume_sent_now=1）
```

### 数据库写入时机（chats 表）

每个会话处理过程**只调用一次** `upsert_chat`，在操作前（阶段二）完成：

```
阶段一：read_messages() + 分析  ← 纯读取，无写库
阶段二：upsert_chat(全字段，resume_sent=当前状态)  ← 唯一一次写库
阶段三：execute_session_actions  ← 纯操作，无写库
```

**SQL 保护**（`upsert_chat` 的 ON CONFLICT 逻辑）：
- `resume_sent = MAX(old, new)`：只增不减
- `tendency_score = CASE WHEN new>0 THEN new ELSE old END`：AI 失败（返回0）不覆盖已有评分
- `ai_reasoning = CASE WHEN new!='' THEN new ELSE old END`：同上

### chats 表字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER | 主键，自增 |
| `encrypt_job_id` | TEXT UNIQUE | 对应 `jobs.job_id`（encryptJobId） |
| `jobs_db_id` | INTEGER | 对应 `jobs.id`，0 表示未匹配 |
| `boss_name` | TEXT | Boss 姓名（来自 `window.chat.communicating.name`） |
| `company` | TEXT | 公司名称 |
| `boss_title` | TEXT | Boss 职位（招聘者/HR等） |
| `initiator` | TEXT | 发起方：`"me"`=我主动 / `"boss"`=对方主动 |
| `salary_desc` | TEXT | 薪资描述（来自 `window.chat.communicating.salaryDesc`，明文未混淆，如 "25-50K·14薪"） |
| `salary_low` | INTEGER | 薪资下限（来自 `lowSalary`，原始数值） |
| `salary_high` | INTEGER | 薪资上限（来自 `highSalary`，原始数值） |
| `chat_history` | TEXT | JSON 数组，每条含 from/text/time/status/isCard |
| `resume_sent` | INTEGER | 0=未发 1=已发（任何方式） |
| `tendency_score` | INTEGER | AI 倾向评分 0-100 |
| `ai_reasoning` | TEXT | 评分理由（一句话） |
| `created_at` | TEXT | 首次写入时间 |
| `updated_at` | TEXT | 最后更新时间 |

---

## 注意事项

- **不要以管理员身份运行** `start_chrome_job.bat`，否则 Chrome 附加 `--no-sandbox` 导致行为异常
- **每次重启 Chrome** 需重新手动登录，登录状态保存在 `browser_data/`
- **随机延时**：卡片点击后等 1.5–2.5s，卡片间隔 1–3s，避免触发频率限制
- **DB 去重局限**：以 JD 全文精确匹配，内容差一字即视为新岗位；沟通失败（`greeted=0`）的历史记录不会自动重试
- **城市字段为空时不过滤**：避免因 DOM 变化读不到城市而误跳过北京岗位
- **BOSS 投递限制**：对方未回复时无法投递简历，这是平台规则
