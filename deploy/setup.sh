#!/bin/bash
# ═══════════════════════════════════════════════════════════
# 无界AI超级员工系统 — 阿里云一键部署脚本
# 适用: Ubuntu 22.04 / 24.04 LTS
# ═══════════════════════════════════════════════════════════
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}无界AI超级员工系统 — 阿里云部署${NC}"
echo -e "${GREEN}========================================${NC}"

# ── 0. 更新系统 ───────────────────────────────────────────
echo -e "${YELLOW}[1/7] 更新系统包...${NC}"
sudo apt update && sudo apt upgrade -y

# ── 1. 安装基础依赖 ──────────────────────────────────────
echo -e "${YELLOW}[2/7] 安装 Python / Node / Nginx / Docker...${NC}"
sudo apt install -y python3 python3-pip python3-venv nginx curl git

# Node.js 20.x
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Docker
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER

# ── 2. 拉取代码 ──────────────────────────────────────────
echo -e "${YELLOW}[3/7] 拉取项目代码...${NC}"
APP_DIR=/opt/unbounded-ai
sudo mkdir -p $APP_DIR
sudo chown $USER:$USER $APP_DIR

if [ -d "$APP_DIR/.git" ]; then
    cd $APP_DIR && git pull
else
    git clone https://github.com/hxqsmj/unbounded-ai-system.git $APP_DIR
fi

cd $APP_DIR

# ── 3. 配置环境变量 ──────────────────────────────────────
echo -e "${YELLOW}[4/7] 配置环境变量...${NC}"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "请编辑 $APP_DIR/.env 填入 API Key 后重新运行"
fi

# ── 4. 安装 Python 依赖 ──────────────────────────────────
echo -e "${YELLOW}[5/7] 安装 Python 依赖...${NC}"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# ── 5. 构建前端 ──────────────────────────────────────────
echo -e "${YELLOW}[6/7] 构建前端...${NC}"
cd $APP_DIR/frontend
npm install
npm run build
cd $APP_DIR

# ── 6. 配置 Nginx ────────────────────────────────────────
echo -e "${YELLOW}[7/7] 配置 Nginx...${NC}"
sudo cp $APP_DIR/deploy/nginx.conf /etc/nginx/sites-available/unbounded-ai
sudo ln -sf /etc/nginx/sites-available/unbounded-ai /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# ── 7. 配置 systemd ──────────────────────────────────────
sudo cp $APP_DIR/deploy/unbounded-ai.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable unbounded-ai

# ── 8. 启动基础设施 ──────────────────────────────────────
echo -e "${YELLOW}启动 Docker 服务...${NC}"
docker compose up -d
sleep 5

# ── 9. 导入知识库 ────────────────────────────────────────
echo -e "${YELLOW}导入知识库...${NC}"
source .venv/bin/activate
python scripts/import_to_qdrant.py data/real_sales_faq.csv
python scripts/import_to_qdrant.py data/real_sales_faq_v2.csv

# ── 10. 启动后端 ─────────────────────────────────────────
sudo systemctl start unbounded-ai

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}🎉 部署完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "后端: http://服务器IP:8001"
echo "前端: http://服务器IP"
echo "API文档: http://服务器IP:8001/docs"
echo "审核面板: http://服务器IP"
echo ""
echo "查看日志: sudo journalctl -u unbounded-ai -f"
echo "重启服务: sudo systemctl restart unbounded-ai"
