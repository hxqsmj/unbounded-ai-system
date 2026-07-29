# 🔗 微信 Hook 客户端对接指南

## 架构概览

```
[微信 Hook 客户端] ──WSS──▶ [Ngrok 公网] ──TCP──▶ [WSS Gateway :8765] ──HTTP──▶ [AI Brain :8001]
     (Windows)                 0.tcp.ngrok.io:XXXXX           (本机)                    (本机)
```

## 第一步: 启动后端服务

```bash
# 终端 1 — 启动 FastAPI 后端
cd E:\零码\无界AI超级员工系统
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001

# 终端 2 — 启动 WSS 网关 (监听 0.0.0.0 以便外部访问)
python scripts/start_gateway.py --host 0.0.0.0 --port 8765

# 终端 3 — 启动前端 (可选)
cd frontend && npm run dev
```

## 第二步: 启动内网穿透

```bash
# 方式 A: ngrok (推荐，最简)
ngrok start --all --config=config/ngrok.yml

# 方式 B: frp (自建服务器，更稳定)
frpc -c config/frpc.ini
```

启动后会看到类似输出:

```
Forwarding  tcp://0.tcp.ngrok.io:12345 -> localhost:8765
                          ^^^^^ ^^^^^
                          host   port
```

记住这个地址: `0.tcp.ngrok.io:12345`

## 第三步: 配置微信 Hook 客户端

在 Hook 工具的配置文件中修改 WSS 推送地址:

```
# ===== 旧配置 (本地) =====
wss_url = ws://127.0.0.1:8765/ws/hook/sales_01

# ===== 新配置 (公网) =====
wss_url = ws://0.tcp.ngrok.io:12345/ws/hook/sales_01
```

**多账号配置** (每个销售账号一条):
```
wss_url_sales_01 = ws://0.tcp.ngrok.io:12345/ws/hook/sales_01
wss_url_sales_02 = ws://0.tcp.ngrok.io:12345/ws/hook/sales_02
wss_url_sales_03 = ws://0.tcp.ngrok.io:12345/ws/hook/sales_03
```

## 第四步: 验证连通性

```bash
# 运行验收测试 (替换为你的 ngrok 地址)
python scripts/final_test.py --host 0.tcp.ngrok.io --port 12345
```

## 第五步: 见证奇迹

1. 用手机微信向已登录的微信号发一条消息:
   > "你好，我们工厂要做环氧地坪，大概5000平，多少钱？"

2. 观察 `http://localhost:5173` 前端审核面板:
   - 左侧队列自动弹出新消息 ✨
   - AI 已生成专业回复 💬
   - 点击「一键采纳」完成发送 ✅

## 故障排查

| 问题 | 检查 |
|------|------|
| Hook 连不上 | `curl http://localhost:8765` 应返回 426 |
| ngrok 不通 | `ngrok start --all` 是否在运行 |
| 消息不弹 | 后端 `:8001/health` 是否 OK |
| 前端无响应 | Vite 是否在 `:5173` 运行 |
