#!/bin/bash
# ═══════════════════════════════════════════════════════════
# 无界AI — 阿里云 Linux 一键部署 (dnf 版)
# ═══════════════════════════════════════════════════════════
set -e

APP_DIR=/opt/unbounded-ai-system
GREEN='\033[1;32m'
NC='\033[0m'

echo -e "${GREEN}=== [1/8] 安装依赖 ===${NC}"
sudo dnf install -y docker python3 python3-pip nginx git nodejs || true
sudo systemctl enable --now docker 2>/dev/null || true

echo -e "${GREEN}=== [2/8] 配置 Python ===${NC}"
cd $APP_DIR
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

echo -e "${GREEN}=== [3/8] 构建前端 ===${NC}"
cd $APP_DIR/frontend
npm install && npm run build
cd $APP_DIR

echo -e "${GREEN}=== [4/8] 配置 .env ===${NC}"
if [ ! -f .env ]; then cp .env.example .env; fi
echo "请编辑 $APP_DIR/.env 填入 LLM_API_KEY 和 EMBEDDING_API_KEY"
read -p "按 Enter 继续 (先用默认配置)... "

echo -e "${GREEN}=== [5/8] 启动 Docker 服务 ===${NC}"
docker compose up -d 2>/dev/null || true
sleep 5

echo -e "${GREEN}=== [6/8] Nginx ===${NC}"
sudo cp $APP_DIR/deploy/nginx.conf /etc/nginx/nginx.conf 2>/dev/null || sudo cp $APP_DIR/deploy/nginx.conf /etc/nginx/conf.d/unbounded-ai.conf
sudo systemctl enable --now nginx 2>/dev/null || true

echo -e "${GREEN}=== [7/8] systemd 后端 ===${NC}"
sudo cp $APP_DIR/deploy/unbounded-ai.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable unbounded-ai
sudo systemctl start unbounded-ai 2>/dev/null || true

echo -e "${GREEN}=== [8/8] 导入知识库 ===${NC}"
source .venv/bin/activate
python scripts/import_to_qdrant.py data/real_sales_faq.csv 2>/dev/null || echo "Qdrant未就绪，稍后手动导入"
python scripts/import_to_qdrant.py data/real_sales_faq_v2.csv 2>/dev/null || echo "Qdrant未就绪，稍后手动导入"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  🎉 部署完成！${NC}"
echo -e "${GREEN}========================================${NC}"
