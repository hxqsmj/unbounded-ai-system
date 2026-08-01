#!/bin/bash
# ═══════════════════════════════════════════════════════════
# 无界AI超级员工系统 — 阿里云一键部署脚本 (V4.0 生产级)
# 适用: Ubuntu 22.04 / 24.04 LTS
# 用法: chmod +x deploy/setup.sh && ./deploy/setup.sh
# ═══════════════════════════════════════════════════════════
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}无界AI超级员工系统 — 阿里云部署${NC}"
echo -e "${GREEN}V4.0 生产级${NC}"
echo -e "${GREEN}========================================${NC}"

APP_DIR=/opt/unbounded-ai

# ── 1. 系统更新 + 基础依赖 ───────────────────────────────
echo -e "${YELLOW}[1/10] 安装系统依赖 (Python / Node / Nginx / Docker)...${NC}"
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv nginx curl git

# Node.js 20.x
if ! command -v node &>/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt install -y nodejs
fi
echo "   Node.js: $(node -v)"

# Docker + Docker Compose
if ! command -v docker &>/dev/null; then
    echo -e "${YELLOW}安装 Docker...${NC}"
    curl -fsSL https://get.docker.com | sudo bash
    sudo usermod -aG docker $USER
fi
echo "   Docker: $(docker --version)"

# ── 2. 拉取代码 ──────────────────────────────────────────
echo -e "${YELLOW}[2/10] 拉取项目代码...${NC}"
sudo mkdir -p $APP_DIR
sudo chown $USER:$USER $APP_DIR

if [ -d "$APP_DIR/.git" ]; then
    cd $APP_DIR && git pull
else
    git clone https://github.com/hxqsmj/unbounded-ai-system.git $APP_DIR
fi

cd $APP_DIR

# ── 3. 配置环境变量 ──────────────────────────────────────
echo -e "${YELLOW}[3/10] 配置环境变量...${NC}"
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}⚠ 必须编辑 $APP_DIR/.env${NC}"
    echo -e "${RED}  1. 填入 LLM_API_KEY (DeepSeek)${NC}"
    echo -e "${RED}  2. 填入 EMBEDDING_API_KEY (SiliconFlow)${NC}"
    echo -e "${RED}  3. 确认 DEBUG=false${NC}"
    echo -e "${RED}  4. 修改 PostgreSQL 默认密码!${NC}"
    echo -e "${RED}========================================${NC}"
    echo ""
    read -p "编辑完成后按 Enter 继续..."
fi

# 强制确保 DEBUG=false
if grep -q 'DEBUG=true' .env 2>/dev/null; then
    echo -e "${RED}⚠ 检测到 DEBUG=true，已自动改为 false${NC}"
    sed -i 's/DEBUG=true/DEBUG=false/' .env
fi

# ── 4. 安装 Python 依赖 ──────────────────────────────────
echo -e "${YELLOW}[4/10] 安装 Python 依赖...${NC}"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# ── 5. 构建前端 ──────────────────────────────────────────
echo -e "${YELLOW}[5/10] 构建前端...${NC}"
cd $APP_DIR/frontend
npm install
npm run build
cd $APP_DIR

# ── 6. 配置 Nginx ────────────────────────────────────────
echo -e "${YELLOW}[6/10] 配置 Nginx...${NC}"
sudo cp $APP_DIR/deploy/nginx.conf /etc/nginx/sites-available/unbounded-ai
sudo ln -sf /etc/nginx/sites-available/unbounded-ai /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
echo "   Nginx: ✓"

# ── 7. 配置 systemd 后端服务 ─────────────────────────────
echo -e "${YELLOW}[7/10] 配置后端服务...${NC}"
sudo cp $APP_DIR/deploy/unbounded-ai.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable unbounded-ai

# ── 8. 启动 Docker 基础设施 ──────────────────────────────
echo -e "${YELLOW}[8/10] 启动 Docker 服务 (Qdrant + MongoDB + PostgreSQL + Redis)...${NC}"
docker compose up -d
echo "   等待数据库就绪..."
sleep 8

# 检查所有容器
ALL_OK=true
for svc in qdrant mongodb postgres redis; do
    if docker ps --format '{{.Names}}' | grep -q "unbounded_${svc}"; then
        echo "   ✓ ${svc}"
    else
        echo -e "   ${RED}✗ ${svc} 未运行${NC}"
        ALL_OK=false
    fi
done

if [ "$ALL_OK" = false ]; then
    echo -e "${RED}部分容器启动失败，查看: docker compose logs${NC}"
fi

# ── PostgreSQL 表初始化 ──────────────────────────────────
echo ""
echo -e "${YELLOW}初始化 PostgreSQL 数据表...${NC}"
# 从 .env 读取 PG 配置
PG_USER=$(grep '^PG_USER=' .env 2>/dev/null | cut -d= -f2 || echo "geop")
PG_DB=$(grep '^PG_DB=' .env 2>/dev/null | cut -d= -f2 || echo "unbounded_ai")

# ⚠ 表结构与 app/services/human_loop.py 的建表语句保持一致！
#   之前版本列名不一致 (original_question/ai_reply...) 导致数据飞轮静默写入失败
docker exec unbounded_postgres psql -U "${PG_USER}" -d "${PG_DB}" -c "
CREATE TABLE IF NOT EXISTS rag_feedback (
    id SERIAL PRIMARY KEY,
    trace_id VARCHAR(64) UNIQUE NOT NULL,
    context_text TEXT NOT NULL,
    ai_raw_output TEXT NOT NULL,
    human_edited_output TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT NOW()
);" 2>/dev/null && echo "   PostgreSQL: rag_feedback ✓" || echo "   ⚠ 表初始化跳过（可能已存在）"

# 兼容旧表: 若服务器上存在旧结构的 rag_feedback 表，先删除重建（数据为反馈样本，无业务价值）
OLD_COLS=$(docker exec unbounded_postgres psql -U "${PG_USER}" -d "${PG_DB}" -tAc \
  "SELECT count(*) FROM information_schema.columns WHERE table_name='rag_feedback' AND column_name='original_question';" 2>/dev/null || echo "0")
if [ "$OLD_COLS" = "1" ]; then
    echo -e "${YELLOW}   检测到旧版 rag_feedback 表结构，删除重建以匹配新结构...${NC}"
    docker exec unbounded_postgres psql -U "${PG_USER}" -d "${PG_DB}" -c "DROP TABLE rag_feedback;"
    docker exec unbounded_postgres psql -U "${PG_USER}" -d "${PG_DB}" -c "
CREATE TABLE IF NOT EXISTS rag_feedback (
    id SERIAL PRIMARY KEY,
    trace_id VARCHAR(64) UNIQUE NOT NULL,
    context_text TEXT NOT NULL,
    ai_raw_output TEXT NOT NULL,
    human_edited_output TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT NOW()
);" && echo "   PostgreSQL: rag_feedback 已重建 ✓"
fi

# ── 9. 导入知识库 ────────────────────────────────────────
echo ""
echo -e "${YELLOW}[9/10] 导入知识库到 Qdrant...${NC}"
source .venv/bin/activate
python scripts/import_to_qdrant.py data/real_sales_faq.csv 2>/dev/null && echo "   FAQ v1: ✓" || echo "   ⚠ FAQ v1 导入失败"
python scripts/import_to_qdrant.py data/real_sales_faq_v2.csv 2>/dev/null && echo "   FAQ v2: ✓" || echo "   ⚠ FAQ v2 导入失败"

# ── 10. 启动后端 ─────────────────────────────────────────
echo ""
echo -e "${YELLOW}[10/10] 启动后端服务...${NC}"
sudo systemctl restart unbounded-ai
sleep 3

# ── 健康检查 ─────────────────────────────────────────────
echo ""
echo -e "${YELLOW}健康检查...${NC}"

HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/health 2>/dev/null || echo "000")
if [ "$HEALTH" = "200" ]; then
    echo -e "   ${GREEN}✓ 后端 API 正常 (HTTP $HEALTH)${NC}"
else
    echo -e "   ${RED}✗ 后端 API 异常 (HTTP $HEALTH)${NC}"
    echo "   查看日志: sudo journalctl -u unbounded-ai -n 30"
fi

FRONTEND=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1/ 2>/dev/null || echo "000")
if [ "$FRONTEND" = "200" ]; then
    echo -e "   ${GREEN}✓ 前端正常 (HTTP $FRONTEND)${NC}"
else
    echo -e "   ${RED}✗ 前端异常 (HTTP $FRONTEND)${NC}"
fi

# ═══════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}🎉 部署完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "  前端页面:   http://$(hostname -I | awk '{print $1}')"
echo "  审核面板:   http://$(hostname -I | awk '{print $1}')"
echo "  API 文档:   http://$(hostname -I | awk '{print $1}'):8001/docs"
echo ""
echo "--- 常用命令 ---"
echo "  后端日志:    sudo journalctl -u unbounded-ai -f"
echo "  重启后端:    sudo systemctl restart unbounded-ai"
echo "  Nginx 日志:  sudo tail -f /var/log/nginx/access.log"
echo "  Docker 状态: docker compose -f $APP_DIR/docker-compose.yml ps"
echo ""
echo "--- ⚠ 下一步（重要） ---"
echo "  1. 配置防火墙:    sudo bash $APP_DIR/deploy/firewall.sh"
echo "  2. 阿里云安全组:  仅开放 80 (HTTP) + 22 (SSH)"
echo "  3. 修改 PG 密码:  编辑 .env 中的 PG_PASSWORD, 然后重建容器"
echo ""
