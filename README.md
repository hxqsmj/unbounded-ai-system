# 🤖 无界AI超级员工系统 — 嘉兴晨邦版 (V4.0 工业级)

全异步、高防封、人机协作智能客服平台。基于 **FastAPI + Qdrant + MongoDB + Redis + PostgreSQL** 构建，前端 **Vue3 + Element Plus + Tailwind CSS** 精装审核面板。

---

## 一、 系统架构总览

```
[微信 Hook 客户端] ──WSS──▶ [Ngrok 穿透] ──TCP──▶ [WSS Gateway :8765] ──HTTP──▶ [FastAPI 后端 :8001]
   (Windows 微信)            0.tcp.ngrok.io:XXXX         (V4.0 双向网关)              (AI Brain 引擎)
                                                                                       │
         ┌─────────────────────────────────────────────────────────────────────────────┤
         │                                                                             │
         ▼ (Qdrant :6333)                                                    ▼ (Vue3 面板 :5173)
  [71条晨邦地坪 FAQ]                                                  [人机协作审核工作台]
  (BGE-M3 1024维向量)                                             (ACCEPT / MODIFY / REJECT)
                                                                              │
                                                                              ▼
                                                                   [TypingSimulator 延时]
                                                                      (Redis 队列 :6379)
                                                                              │
                                                                   ┌──────────┴──────────┐
                                                                   ▼                     ▼
                                                            [指令回传 Hook 发送]   [PostgreSQL 落盘]
                                                                                   (rag_feedback 飞轮)
```

---

## 二、 项目目录结构

```
无界AI超级员工系统/
├── app/                                # 后端核心
│   ├── core/
│   │   ├── config.py                   # 全局配置 (pydantic-settings + .env)
│   │   └── schemas.py                  # Pydantic V2 数据模型
│   ├── services/
│   │   ├── wss_gateway.py              # WSS Gateway V4.0 (双向长连接网关)
│   │   ├── ai_brain.py                 # RAG 检索 + LLM 生成引擎
│   │   ├── human_loop.py               # 人机协作审核 + PG 数据飞轮
│   │   └── typing_simulator.py         # 拟人打字延时 + Redis 队列
│   └── main.py                         # FastAPI 入口 + WebSocket 推送
│
├── frontend/                           # Vue3 前端审核面板
│   ├── src/
│   │   ├── api/chat.js                 # Axios REST API 封装
│   │   ├── composables/
│   │   │   ├── useWebSocket.js         # WS 实时推送 + 断线重连
│   │   │   ├── useAudio.js             # Web Audio API 音效
│   │   │   └── useKeyboard.js          # 全局快捷键系统
│   │   ├── components/
│   │   │   ├── QueuePanel.vue          # 左侧待审核队列
│   │   │   └── AuditPanel.vue          # 右侧审核详情
│   │   ├── App.vue                     # 根布局 (暗黑/明亮双模)
│   │   ├── main.js                     # 入口 (Element Plus 注册)
│   │   └── style.css                   # Tailwind + CSS 变量设计系统
│   └── vite.config.js                  # Vite 代理配置
│
├── scripts/                            # 运维与测试脚本集
│   ├── start_gateway.py                # WSS 网关一键启动
│   ├── import_to_qdrant.py             # FAQ 语料灌入 Qdrant
│   ├── verify_qdrant.py                # RAG 检索验证
│   ├── mock_hook_client.py             # 微信消息推送模拟器
│   ├── e2e_full_chain.py               # 5 阶段全链路自动化测试
│   ├── final_test.py                   # P1 最终验收脚本
│   └── verify_pg_flywheel.py           # PG 数据飞轮验证
│
├── tests/                              # 单元测试
│   ├── test_all.py                     # 16 项 Mock 全模块验证
│   └── mock_hook_client.py             # 模拟微信 Hook 客户端
│
├── config/
│   └── ngrok.yml                       # Ngrok 内网穿透配置
├── docs/
│   └── HOOK_SETUP.md                   # 微信 Hook 客户端对接指南
├── data/
│   ├── real_sales_faq.csv              # 51 条嘉兴晨邦专属地坪 FAQ
│   └── sales_knowledge.csv             # 原始销售知识库
├── .env                                # 环境变量 (API Key / 数据库)
├── .env.example                        # 环境变量模板
├── docker-compose.yml                  # Qdrant + MongoDB + Redis
├── requirements.txt                    # Python 依赖
└── README.md                           # 本文件
```

---

## 三、 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/docs` | Swagger 文档 |
| `POST` | `/api/v1/chat/generate` | AI 生成 (RAG + LLM) |
| `POST` | `/api/v1/chat/confirm_send` | 人工确认/修改/拒绝 |
| `GET` | `/api/v1/chat/pending` | 待审核队列 |
| `GET` | `/api/v1/chat/trace/{id}` | Trace 详情 (含 RAG 上下文) |
| `GET` | `/api/v1/chat/stats` | 审核数据统计 |
| `WS` | `/ws` | 前端实时推送 |

---

## 四、 快速启动

### 1. 安装依赖

```bash
cd E:\零码\无界AI超级员工系统
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

### 2. 配置环境变量

```bash
copy .env.example .env
# 编辑 .env 填入 API Key 和数据库连接
```

### 3. 启动基础设施 (Docker)

```bash
docker compose up -d
# 启动 Qdrant (:6333) + MongoDB (:27017) + Redis (:6379)
```

### 4. 导入知识库

```bash
python scripts/import_to_qdrant.py data/real_sales_faq.csv
# 51 条晨邦 FAQ → 1024 维向量 → Qdrant
```

### 5. 启动服务 (三个终端)

```bash
# 终端 1 — FastAPI 后端
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

# 终端 2 — WSS Gateway
python scripts/start_gateway.py --host 0.0.0.0 --port 8765

# 终端 3 — Vue3 前端
cd frontend && npm run dev
```

### 6. 一键测试

```bash
# Mock 全模块测试 (无需外部服务)
python tests/test_all.py

# 端到端全链路自动化测试
python scripts/e2e_full_chain.py

# P1 最终验收
python scripts/final_test.py
```

---

## 五、 跨设备上线 (内网穿透)

```bash
# 1. 启动 Ngrok
ngrok start --all --config=config/ngrok.yml
# → 获取公网地址: 0.tcp.ngrok.io:XXXXX

# 2. 在微信 Hook 客户端配置
# wss_url = ws://0.tcp.ngrok.io:XXXXX/ws/hook/sales_01

# 3. 验收外网链路
python scripts/final_test.py --host 0.tcp.ngrok.io --port XXXXX
```

详见 `docs/HOOK_SETUP.md`

---

## 六、 运维命令速查

| 需求 | 命令 |
|------|------|
| 更新知识库 | `python scripts/import_to_qdrant.py data/real_sales_faq.csv` |
| RAG 检索验证 | `python scripts/verify_qdrant.py -q "地坪报价"` |
| 模拟微信发消息 | `python scripts/mock_hook_client.py` |
| WSS 网关模式 | `python scripts/mock_hook_client.py --wss` |
| 全链路测试 | `python scripts/e2e_full_chain.py` |
| 最终验收 | `python scripts/final_test.py` |
| 数据飞轮验证 | `python scripts/verify_pg_flywheel.py` |
| Mock 单元测试 | `python tests/test_all.py` |

---

## 七、 核心指标

| 指标 | 数值 |
|------|------|
| 知识库容量 | **71 条** (51晨邦 + 20 通用) |
| 向量模型 | BGE-M3 (1024 维) |
| 平均检索置信度 | **0.845** |
| LLM 模型 | DeepSeek-Chat |
| 网关心跳 | 30s Ping/Pong |
| 断线重连 | 缓冲队列 + 恢复投递 |
| 数据飞轮 | PostgreSQL rag_feedback |

---

## 八、 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| 向量检索 | Qdrant (1024维) |
| Trace 日志 | MongoDB (motor) |
| 数据飞轮 | PostgreSQL (asyncpg) |
| 延迟队列 | Redis (redis.asyncio) |
| HTTP 客户端 | httpx |
| WebSocket | websockets 15.x |
| 前端框架 | Vue 3 + Vite |
| UI 组件 | Element Plus + Tailwind CSS |
| 数据模型 | Pydantic V2 |

---

## 九、 数据流全闭环

```text
微信 Hook → WSS Gateway :8765
                │
                ▼
        POST /api/v1/chat/generate
                │
        ┌───────┴───────┐
        ▼               ▼
    Qdrant RAG      LLM (DeepSeek)
    (Top-3 检索)     (System Prompt + Context)
        │               │
        └───┬───────────┘
            ▼
    MongoDB trace_logs (PENDING)
            │
            ▼
    WebSocket → Vue3 前端面板
            │
    ┌───────┼───────┐
    ▼       ▼       ▼
  ACCEPT  MODIFY  REJECT
    │       │       │
    │       ├── PG rag_feedback (数据飞轮)
    │       │
    └───┬───┘
        ▼
  TypingSimulator (Redis 延迟队列)
        │
        ▼
  WSS Gateway → Hook 客户端 → 微信发送
```
