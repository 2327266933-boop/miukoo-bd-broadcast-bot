# BD 群发提醒机器人

面向 BD 运营场景的自动消息机器人。使用者只需要告诉机器人“给谁发、发什么类型的消息、多久没回复要提醒”，机器人会自动完成消息发送、回复状态跟踪和超时二次提醒。

现在已提供一个本地 Web 工作台：可以在页面里创建群发任务、预览消息、查看每个 BD 的回复状态、单人停催，并导出回复整理 CSV。

## 目标

- 支持按 BD、BD 分组、门店归属或自定义名单批量发送消息。
- 支持不同业务类型的消息模板，例如库存提醒、价格 Lose 跟进、活动报名、资料补充、任务催办。
- 支持检测收件人是否回复；未回复时按配置间隔自动提醒。
- 支持发送记录、回复记录、提醒记录可追踪，避免重复轰炸和不可审计。
- 支持运营工作台查看每个 BD 的触达、提醒、回复和失败原因。
- 支持输入商家名称查询对应 BD，并将查询结果转成群发名单。
- 支持基础频控、黑名单、免打扰时段，保证内部沟通合规。

## 核心流程

1. 使用者创建发送任务，指定收件人、消息类型、模板变量和提醒策略。
2. 系统解析收件人名单，过滤无效用户、黑名单用户和免打扰用户。
3. 系统根据消息类型匹配模板，渲染出每个 BD 的个性化消息。
4. 机器人通过消息平台发送首条消息，并记录发送状态。
5. 系统监听消息平台回调，识别 BD 是否回复。
6. 如果在指定时间内未收到回复，系统自动发送提醒消息。
7. 达到最大提醒次数、收到回复、任务被取消或超过截止时间后，停止提醒。

## MVP 功能范围

- 创建单次群发任务
- 支持 JSON 配置文件导入收件人
- 支持消息模板和变量替换
- 支持首发消息
- 支持回复 webhook 回调
- 支持未回复自动提醒
- 支持最大提醒次数限制
- 支持任务状态查询
- 支持发送和回复日志
- 支持 Web 工作台创建任务、查看进度、停催和导出回复

## 当前代码实现

本仓库当前实现了一个可本地运行的 Python MVP：

- HTTP API：创建任务、查询任务、取消任务、接收回复 webhook。
- SQLite 持久化：任务、收件人、发送日志、回复日志。
- 模板引擎：按消息类型渲染首发消息和提醒消息。
- Mock 消息适配器：本地打印消息，不真实触达 BD。
- 飞书/Lark 发送适配器：配置密钥后可调用飞书消息 API。
- 飞书事件回调：支持 URL 校验和 `im.message.receive_v1` 用户消息事件。
- CSV 导入：支持从表格导出的 CSV 创建收件人名单。
- 任务预览：正式发送前可预览每个 BD 实际收到的消息。
- Web 工作台：通过浏览器操作群发任务和回复整理。
- 商家查 BD：支持本地 CSV 映射和风神 HTTP 查询两种来源。
- 单人停催：支持对某个收件人停止后续提醒。
- 回复导出：支持导出每个 BD 的回复状态和最近回复内容。
- 基础频控：默认同一 `contact_id` 24 小时最多发送 3 条。
- 后台提醒线程：定时扫描未回复对象，到点后自动提醒。
- 单元测试：覆盖首发、提醒、回复停止提醒、模板变量校验、CSV、预览、停催、频控、工作台页面、回复导出和商家查 BD。

### 目录结构

```text
.
├── .env.example
├── README.md
├── examples/
│   ├── merchant_bd_mapping.csv
│   ├── recipients.inventory.csv
│   └── task.inventory.json
├── miukoo_bot/
│   ├── __main__.py
│   ├── api.py
│   ├── config.py
│   ├── db.py
│   ├── importers.py
│   ├── messaging.py
│   ├── scheduler.py
│   ├── service.py
│   ├── templates.py
│   ├── time_utils.py
│   └── web/
│       └── index.html
├── pyproject.toml
└── tests/
    ├── test_api.py
    └── test_service.py
```

### 本地运行

```bash
python3 -m miukoo_bot --host 127.0.0.1 --port 8080 --adapter mock
```

打开工作台：

```text
http://127.0.0.1:8080/workbench
```

健康检查：

```bash
curl http://127.0.0.1:8080/health
```

创建群发任务：

```bash
curl -X POST http://127.0.0.1:8080/api/tasks \
  -H 'Content-Type: application/json' \
  --data @examples/task.inventory.json
```

预览任务，不真实发送：

```bash
curl -X POST http://127.0.0.1:8080/api/tasks/preview \
  -H 'Content-Type: application/json' \
  --data @examples/task.inventory.json
```

查询任务列表：

```bash
curl http://127.0.0.1:8080/api/tasks
```

导出某个任务的 BD 回复整理：

```bash
curl http://127.0.0.1:8080/api/tasks/{task_id}/replies.csv
```

模拟 BD 回复：

```bash
curl -X POST http://127.0.0.1:8080/api/webhooks/mock/message \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"替换成任务ID","bd_id":"bd_001","content":"已确认"}'
```

模拟飞书 URL 校验：

```bash
curl -X POST http://127.0.0.1:8080/api/webhooks/lark/events \
  -H 'Content-Type: application/json' \
  -d '{"schema":"2.0","header":{"event_type":"url_verification","token":"你的VerificationToken"},"event":{"challenge":"test_challenge"}}'
```

停止某个 BD 后续提醒：

```bash
curl -X POST http://127.0.0.1:8080/api/tasks/{task_id}/recipients/{recipient_id}/stop
```

手动触发一次提醒扫描：

```bash
curl -X POST http://127.0.0.1:8080/api/scheduler/run-once
```

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

### CSV 导入

CSV 文件示例见 `examples/recipients.inventory.csv`：

```csv
bd_id,name,contact_id,group,city,shop_count,deadline
bd_001,张三,mock_user_001,华东一区,上海,12,今天 18:00
bd_002,李四,mock_user_002,华南二区,广州,8,今天 18:00
```

创建任务时使用 `recipients_csv_path`：

```json
{
  "task_name": "8月门店库存确认",
  "channel": "mock",
  "message_type": "inventory_check",
  "recipients_csv_path": "examples/recipients.inventory.csv",
  "follow_up": {
    "enabled": true,
    "first_remind_after_minutes": 120,
    "remind_interval_minutes": 180,
    "max_remind_times": 2
  }
}
```

固定字段包括 `bd_id`、`name`、`contact_id`、`mobile`、`group`、`variables_json`。其他列会自动作为模板变量；以 `variable_` 开头的列会去掉前缀后作为变量名。

## Web 工作台

本地启动服务后打开：

```text
http://127.0.0.1:8080/workbench
```

工作台支持：

- 创建群发任务：填写任务名称、发送渠道、消息类型、提醒策略和 BD 名单。
- 商家查 BD：输入商家名称，查询负责人，并一键填入 BD 名单。
- CSV 名单录入：直接粘贴从表格导出的 CSV。
- JSON 名单录入：适合从系统接口或脚本生成名单。
- 消息预览：正式发送前查看每个 BD 会收到的首发和提醒文案。
- 任务列表：查看任务状态、BD 数量、回复数和失败数。
- 回复整理：逐个查看 BD 的状态、发送次数、提醒次数、最近回复时间和回复内容。
- 单人停催：对某个 BD 停止后续提醒。
- 导出 CSV：下载任务下所有 BD 的回复整理结果。

页面中的“创建并发送”会真实调用当前服务的 `/api/tasks`。如果当前 `channel=lark` 且环境变量已配置飞书密钥，会真实发送飞书消息；测试时建议先使用 `channel=mock`。

## 商家名称查询 BD

工作台里可以先输入商家名称，查询对应 BD，再把结果填入群发名单。查询接口：

```http
POST /api/merchant-bd/lookup
Content-Type: application/json
```

```json
{
  "merchant_names": [
    "上海悦来火锅",
    "广州花城茶餐厅"
  ]
}
```

返回里会包含：

- `results`：每个商家的查询结果，状态包括 `matched`、`ambiguous`、`not_found`。
- `recipients`：已匹配且可直接群发的 BD 收件人列表；同一个 BD 负责多个商家时会自动合并。
- `recipient_count`：最终生成的 BD 收件人数。

### 本地 CSV 查询

默认使用本地 CSV 映射表：

```bash
MERCHANT_BD_LOOKUP_PROVIDER=csv
MERCHANT_BD_MAPPING_CSV=examples/merchant_bd_mapping.csv
```

CSV 示例：

```csv
merchant_name,bd_id,name,contact_id,group,city
上海悦来火锅,bd_001,张三,mock_user_001,华东一区,上海
广州花城茶餐厅,bd_002,李四,mock_user_002,华南二区,广州
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `merchant_name` | 商家名称 |
| `bd_id` | BD 内部 ID |
| `name` | BD 姓名 |
| `contact_id` | 消息平台用户 ID，例如飞书 `open_id` 或 `user_id` |
| `group` | BD 分组或区域 |

### 风神查询

如果要接风神，把查询来源改成 `fengshen`：

```bash
MERCHANT_BD_LOOKUP_PROVIDER=fengshen
FENGSHEN_MERCHANT_BD_LOOKUP_URL=https://your-fengshen-endpoint
FENGSHEN_TIMEOUT_SECONDS=10
```

如果风神直接给长期访问 token，配置：

```bash
FENGSHEN_API_TOKEN=your-token
```

如果风神给的是 client ID 和 client secret/password，配置：

```bash
FENGSHEN_TOKEN_URL=https://your-fengshen-token-endpoint
FENGSHEN_CLIENT_ID=your-client-id
FENGSHEN_CLIENT_SECRET=your-client-secret
FENGSHEN_SCOPE=
```

不要把真实 client ID、secret、password 写进代码、README、CSV 或 GitHub 仓库。部署时放到服务器环境变量或 GitHub/云平台 Secrets。

当前服务会向风神地址发送：

```json
{
  "merchant_names": ["上海悦来火锅"]
}
```

推荐风神返回结构：

```json
{
  "results": [
    {
      "merchant_name": "上海悦来火锅",
      "matches": [
        {
          "merchant_name": "上海悦来火锅",
          "bd_id": "bd_001",
          "name": "张三",
          "contact_id": "ou_xxx",
          "group": "华东一区"
        }
      ]
    }
  ]
}
```

如果风神实际字段名不同，可以在 `miukoo_bot/merchant_lookup.py` 的字段别名里补充。

## 非目标

- 不绕过消息平台权限、频控或安全策略。
- 不支持向未授权对象发送消息。
- 不做无限循环催办。
- 不默认读取私人聊天内容，只处理机器人相关会话和平台回调数据。

## 任务配置示例

```yaml
task_name: "8月门店库存确认"
channel: "lark"
message_type: "inventory_check"

recipients:
  - bd_id: "bd_001"
    name: "张三"
    mobile: "13800000000"
    group: "华东一区"
    variables:
      city: "上海"
      shop_count: 12
      deadline: "今天 18:00"
  - bd_id: "bd_002"
    name: "李四"
    mobile: "13900000000"
    group: "华南二区"
    variables:
      city: "广州"
      shop_count: 8
      deadline: "今天 18:00"

follow_up:
  enabled: true
  first_remind_after_minutes: 120
  remind_interval_minutes: 180
  max_remind_times: 2
  stop_when_replied: true
  quiet_hours:
    start: "21:00"
    end: "09:00"
```

## 消息模板示例

### 首次消息

```text
Hi {name}，请帮忙确认 {city} 负责门店的库存情况。

本次涉及 {shop_count} 家门店，请在 {deadline} 前回复确认结果。
回复“已确认”或直接说明异常情况即可。
```

### 提醒消息

```text
Hi {name}，刚才的库存确认还没有收到回复。

请在 {deadline} 前同步一下进展；如已有异常，也可以直接回复异常原因。
```

## 消息类型

| 类型 | 场景 | 推荐变量 |
| --- | --- | --- |
| `inventory_check` | 门店库存确认 | `city`、`shop_count`、`deadline` |
| `price_lose_follow` | 价格 Lose 跟进 | `shop_name`、`sku_name`、`lose_reason`、`deadline` |
| `campaign_signup` | 活动报名提醒 | `campaign_name`、`signup_deadline`、`benefit` |
| `material_collect` | 资料补充 | `material_name`、`missing_fields`、`deadline` |
| `task_urge` | 通用任务催办 | `task_title`、`owner_name`、`deadline` |
| `merchant_follow_up` | 商家跟进 | `merchant_names`、`merchant_count` |

## 状态机

| 状态 | 说明 |
| --- | --- |
| `pending` | 任务已创建，等待发送 |
| `sending` | 正在发送首条消息 |
| `sent` | 首条消息已发送 |
| `replied` | 已收到 BD 回复 |
| `waiting_follow_up` | 等待提醒触发 |
| `followed_up` | 已发送至少一次提醒 |
| `completed` | 任务完成 |
| `cancelled` | 任务被取消 |
| `failed` | 任务发送失败 |

## 回复识别规则

默认只要 BD 在机器人会话中回复任意内容，即视为已回复，并停止后续提醒。

可选增强规则：

- 只认可包含指定关键词的回复，例如“已确认”“已处理”“收到”。
- 将包含“稍后”“处理中”等内容的回复标记为 `in_progress`，允许后续再次提醒。
- 对异常反馈打标签，例如“缺货”“价格异常”“门店关停”。
- 记录首次回复时间，用于统计响应时长。

## 提醒策略

建议默认策略：

- 首次发送后 2 小时未回复，发送第一次提醒。
- 第一次提醒后 3 小时仍未回复，发送第二次提醒。
- 每个任务最多提醒 2 次。
- 夜间 21:00 到次日 09:00 不发送提醒，顺延到下一个可发送时段。
- 收到任意有效回复后立即停止提醒。
- 对同一 BD 同一天最多发送 3 条机器人消息。

## 数据模型

### Task

| 字段 | 说明 |
| --- | --- |
| `id` | 任务 ID |
| `task_name` | 任务名称 |
| `channel` | 消息渠道 |
| `message_type` | 消息类型 |
| `status` | 任务状态 |
| `created_by` | 创建人 |
| `created_at` | 创建时间 |
| `deadline_at` | 业务截止时间 |

### Recipient

| 字段 | 说明 |
| --- | --- |
| `id` | 收件记录 ID |
| `task_id` | 关联任务 ID |
| `bd_id` | BD 标识 |
| `name` | BD 姓名 |
| `contact_id` | 消息平台用户 ID |
| `status` | 当前状态 |
| `send_count` | 已发送次数 |
| `reply_count` | 已回复次数 |
| `last_sent_at` | 最近发送时间 |
| `last_replied_at` | 最近回复时间 |

### MessageLog

| 字段 | 说明 |
| --- | --- |
| `id` | 消息日志 ID |
| `task_id` | 任务 ID |
| `recipient_id` | 收件记录 ID |
| `message_kind` | `initial` 或 `follow_up` |
| `content` | 实际发送内容 |
| `platform_message_id` | 平台消息 ID |
| `status` | 发送状态 |
| `sent_at` | 发送时间 |

## 推荐技术架构

```text
User Input
   |
   v
Task API  ---> Task Store
   |
   v
Recipient Resolver
   |
   v
Template Renderer
   |
   v
Message Adapter ---> Lark / 企业微信 / 钉钉
   |
   v
Message Log

Platform Webhook ---> Reply Handler ---> Status Updater

Scheduler ---> Follow-up Checker ---> Message Adapter
```

## 模块说明

- `Task API`：创建、查询、取消群发任务。
- `Recipient Resolver`：解析 BD 名单，并映射到消息平台用户 ID。
- `Template Renderer`：根据消息类型和变量生成消息内容。
- `Message Adapter`：封装飞书、企业微信、钉钉等平台的发送 API。
- `Reply Handler`：处理平台 webhook，更新回复状态。
- `Scheduler`：定时扫描未回复记录，触发提醒。
- `Rate Limiter`：控制单人、单任务、单渠道发送频率。
- `Audit Log`：记录发送、提醒、回复和失败原因。

## API 草案

### 创建任务

```http
POST /api/tasks
Content-Type: application/json
```

```json
{
  "task_name": "8月门店库存确认",
  "channel": "lark",
  "message_type": "inventory_check",
  "recipients": [
    {
      "bd_id": "bd_001",
      "name": "张三",
      "contact_id": "ou_xxx",
      "variables": {
        "city": "上海",
        "shop_count": 12,
        "deadline": "今天 18:00"
      }
    }
  ],
  "follow_up": {
    "enabled": true,
    "first_remind_after_minutes": 120,
    "remind_interval_minutes": 180,
    "max_remind_times": 2
  }
}
```

### 查询任务

```http
GET /api/tasks/{task_id}
```

### 预览任务

```http
POST /api/tasks/preview
```

### 商家名称查询 BD

```http
POST /api/merchant-bd/lookup
```

### 导出回复整理

```http
GET /api/tasks/{task_id}/replies.csv
```

### 打开工作台

```http
GET /workbench
```

### 取消任务

```http
POST /api/tasks/{task_id}/cancel
```

### 停止单人提醒

```http
POST /api/tasks/{task_id}/recipients/{recipient_id}/stop
```

### 回复回调

```http
POST /api/webhooks/{channel}/message
```

飞书事件回调建议使用：

```http
POST /api/webhooks/lark/events
```

## 环境变量

```bash
BD_BOT_DATABASE=data/bd_bot.sqlite3
BD_BOT_HOST=127.0.0.1
BD_BOT_PORT=8080
BD_BOT_ADAPTER=mock
BD_BOT_SCHEDULER_INTERVAL_SECONDS=30

LARK_APP_ID=cli_xxx
LARK_APP_SECRET=xxx
LARK_VERIFICATION_TOKEN=xxx
LARK_RECEIVE_ID_TYPE=open_id

MERCHANT_BD_LOOKUP_PROVIDER=csv
MERCHANT_BD_MAPPING_CSV=examples/merchant_bd_mapping.csv
FENGSHEN_MERCHANT_BD_LOOKUP_URL=
FENGSHEN_API_TOKEN=
FENGSHEN_TOKEN_URL=
FENGSHEN_CLIENT_ID=
FENGSHEN_CLIENT_SECRET=
FENGSHEN_SCOPE=
FENGSHEN_TIMEOUT_SECONDS=10

DEFAULT_FIRST_REMIND_AFTER_MINUTES=120
DEFAULT_REMIND_INTERVAL_MINUTES=180
DEFAULT_MAX_REMIND_TIMES=2
DAILY_MESSAGE_LIMIT_PER_CONTACT=3
QUIET_HOURS_START=21:00
QUIET_HOURS_END=09:00
```

本地开发默认使用 `BD_BOT_ADAPTER=mock`。接入飞书时改为：

```bash
BD_BOT_ADAPTER=lark
LARK_APP_ID=cli_xxx
LARK_APP_SECRET=xxx
LARK_VERIFICATION_TOKEN=xxx
LARK_RECEIVE_ID_TYPE=open_id
```

## 飞书真实接入

### 1. 创建飞书自建应用

在飞书开放平台创建企业自建应用，并开启机器人能力。

至少需要配置：

- `App ID`：填入 `LARK_APP_ID`
- `App Secret`：填入 `LARK_APP_SECRET`
- `Verification Token`：填入 `LARK_VERIFICATION_TOKEN`

### 2. 配置权限

在飞书应用后台申请并发布权限：

- 以机器人身份发送消息
- 接收用户发送给机器人的消息事件
- 获取用户 ID 相关权限

如果 BD 名单里的 `contact_id` 是飞书 `open_id`，保持：

```bash
LARK_RECEIVE_ID_TYPE=open_id
```

如果 BD 名单里维护的是飞书 `user_id`，改成：

```bash
LARK_RECEIVE_ID_TYPE=user_id
```

### 3. 配置事件订阅

事件订阅 URL 填：

```text
https://你的公网域名/api/webhooks/lark/events
```

本地调试时可以用内网穿透把 8080 暴露成 HTTPS 地址，例如：

```bash
ngrok http 8080
```

然后在飞书后台填：

```text
https://你的-ngrok-域名/api/webhooks/lark/events
```

当前 MVP 支持未加密事件。飞书事件订阅里先不要开启事件加密；如果开启了加密，当前服务会返回错误，后续需要补 AES 解密逻辑。

### 4. 启动真实发送模式

```bash
export BD_BOT_ADAPTER=lark
export LARK_APP_ID=cli_xxx
export LARK_APP_SECRET=xxx
export LARK_VERIFICATION_TOKEN=xxx
export LARK_RECEIVE_ID_TYPE=open_id

python3 -m miukoo_bot --host 0.0.0.0 --port 8080
```

### 5. 创建真实群发任务

任务里的 `channel` 使用 `lark`，`contact_id` 填飞书用户的 `open_id` 或 `user_id`，并与 `LARK_RECEIVE_ID_TYPE` 保持一致。

```json
{
  "task_name": "BD任务提醒",
  "channel": "lark",
  "message_type": "task_urge",
  "recipients": [
    {
      "bd_id": "bd_001",
      "name": "张三",
      "contact_id": "ou_xxx",
      "variables": {
        "task_title": "确认门店库存",
        "owner_name": "张三",
        "deadline": "今天 18:00"
      }
    }
  ],
  "follow_up": {
    "enabled": true,
    "first_remind_after_minutes": 120,
    "remind_interval_minutes": 180,
    "max_remind_times": 2
  }
}
```

## 发送前校验

每次任务启动前必须校验：

- 收件人是否存在平台用户 ID。
- 消息模板变量是否完整。
- 同一 BD 是否已在相近时间收到同类型消息。
- 是否命中黑名单、免打扰或离职状态。
- 当前时间是否在允许发送时段。
- 任务是否设置最大提醒次数。

## 失败处理

| 失败类型 | 处理方式 |
| --- | --- |
| 用户不存在 | 标记失败，写入失败原因 |
| 平台限流 | 延迟重试，最多 3 次 |
| 模板变量缺失 | 阻断该收件人发送 |
| webhook 验签失败 | 拒绝处理并记录安全日志 |
| 数据库写入失败 | 不发送或进入补偿队列，避免状态不一致 |

## 后续迭代

- 支持 Excel 导入和导出发送结果。
- 支持按 BD 维度查看响应率和平均回复时长。
- 支持不同业务线维护自己的模板。
- 支持 AI 改写消息语气，但必须保留关键业务变量。
- 支持异常回复自动归类和汇总。

## 合规与风控

- 仅用于内部授权范围内的 BD 沟通。
- 不发送营销骚扰、无关内容或未授权消息。
- 所有发送、提醒、回复处理都需要保留审计日志。
- 必须支持任务取消和单人停止提醒。
- 默认启用频控和免打扰时段。
