# 🎛️ 无界AI · 人机协作审核面板

基于 Vue 3 + Vite + Element Plus + Tailwind CSS 构建的人机协作审核前端。

## 快速启动

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173

## 架构说明

```
frontend/
├── src/
│   ├── api/
│   │   └── chat.js              # Axios API 封装 (REST)
│   ├── composables/
│   │   └── useWebSocket.js      # WebSocket 实时推送 + 断线重连
│   ├── components/
│   │   ├── QueuePanel.vue       # 左侧待审核队列
│   │   └── AuditPanel.vue       # 右侧审核详情
│   ├── App.vue                  # 根布局 (左右分栏)
│   ├── main.js                  # 入口 (注册 Element Plus)
│   └── style.css                # 全局样式 + Tailwind
├── index.html
├── vite.config.js               # Vite 配置 (含 API 代理)
└── package.json
```

## 接口代理

开发模式下 Vite 自动代理:

| 前端请求 | 代理目标 |
|----------|----------|
| `/api/*` | `http://localhost:8001` |
| `/ws`    | `ws://localhost:8001`   |

## 操作流程

1. **左侧队列** — 显示所有待审核（PENDING）的 AI 生成回复
2. **点击消息** — 右侧展示客户原始提问 + AI 建议回复 + RAG 检索上下文
3. **编辑回复** — 可在 Textarea 中修改 AI 回复
4. **操作按钮**:
   - 🟢 **一键采纳** — 直接发送 AI 回复
   - 🟡 **修改并发送** — 保存人工修改 + 触发数据飞轮
   - 🔴 **拒绝** — 不发送，标记为 CANCELLED
5. **实时推送** — WebSocket 接收新消息，自动刷新队列
