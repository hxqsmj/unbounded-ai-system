# 🚀 无界AI超级员工系统 — 阿里云部署指南

## 一、服务器要求

| 项目 | 最低配置 | 推荐配置 |
|------|----------|----------|
| CPU | 2核 | 4核 |
| 内存 | 4GB | 8GB |
| 系统 | Ubuntu 22.04 | Ubuntu 24.04 |
| 磁盘 | 40GB | 80GB+ |
| 带宽 | 3Mbps | 5Mbps+ |

## 二、购买阿里云 ECS

1. 登录 [阿里云控制台](https://ecs.console.aliyun.com)
2. 创建实例 → **Ubuntu 22.04**
3. 安全组开放端口: **80** (HTTP) + **443** (HTTPS)
4. 获取公网 IP，SSH 登录

## 三、一键部署

```bash
# 1. SSH 登录服务器
ssh root@你的服务器IP

# 2. 安装 git 并克隆项目
apt install -y git
git clone https://github.com/hxqsmj/unbounded-ai-system.git /opt/unbounded-ai

# 3. 配置 .env (填入你的 API Key)
cd /opt/unbounded-ai
cp .env.example .env
nano .env   # 编辑 LLM_API_KEY, EMBEDDING_API_KEY 等

# 4. 一键部署
chmod +x deploy/setup.sh
./deploy/setup.sh
```

## 四、手动部署步骤

如果一键脚本失败，逐步执行：

```bash
# ── 基础依赖
apt update && apt install -y python3 python3-pip python3-venv nginx
curl -fsSL https://get.docker.com | bash

# ── 虚拟环境
cd /opt/unbounded-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# ── 前端构建
cd frontend && npm install && npm run build && cd ..

# ── 基础设施 (Docker)
docker compose up -d

# ── 知识库导入
source .venv/bin/activate
python scripts/import_to_qdrant.py data/real_sales_faq.csv
python scripts/import_to_qdrant.py data/real_sales_faq_v2.csv

# ── Nginx
cp deploy/nginx.conf /etc/nginx/sites-available/unbounded-ai
ln -sf /etc/nginx/sites-available/unbounded-ai /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# ── 启动后端
cp deploy/unbounded-ai.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now unbounded-ai
```

## 五、验证

```bash
# 后端
curl http://localhost:8001/health

# 前端 (Nginx)
curl http://localhost/

# 看板
curl http://localhost:8001/api/v1/chat/dashboard

# 服务状态
systemctl status unbounded-ai
docker ps
```

## 六、HTTPS 配置 (推荐)

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d 你的域名.com
```

## 七、运维命令

| 操作 | 命令 |
|------|------|
| 查看后端日志 | `journalctl -u unbounded-ai -f` |
| 重启后端 | `systemctl restart unbounded-ai` |
| 重启 Nginx | `systemctl reload nginx` |
| 更新代码 | `cd /opt/unbounded-ai && git pull && systemctl restart unbounded-ai` |
| 重新构建前端 | `cd /opt/unbounded-ai/frontend && npm run build` |
| 更新知识库 | `source .venv/bin/activate && python scripts/import_to_qdrant.py data/新文件.csv` |
