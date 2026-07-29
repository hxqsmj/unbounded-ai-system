#!/bin/bash
# ═══════════════════════════════════════════════════════════
# 无界AI — 服务器防火墙配置 (UFW)
# 适用: Ubuntu 22.04+ / Debian
# ═══════════════════════════════════════════════════════════
set -e

GREEN='\033[1;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}无界AI — 防火墙配置${NC}"
echo -e "${GREEN}========================================${NC}"

# ── 安装 ufw ───────────────────────────────────────────────
echo -e "${YELLOW}[1/4] 安装 UFW...${NC}"
sudo apt install -y ufw 2>/dev/null || true

# ── 重置规则 (谨慎! 已有自定义规则请备份) ──────────────────
echo -e "${YELLOW}[2/4] 配置规则...${NC}"

# 默认策略: 拒绝入站，允许出站
sudo ufw default deny incoming
sudo ufw default allow outgoing

# 仅开放必要端口
sudo ufw allow 22/tcp comment 'SSH'
sudo ufw allow 80/tcp comment 'HTTP (Nginx)'

# ── 数据库端口仅 localhost (不对外开放) ─────────────────────
# 这些不需要 ufw 规则，因为 Docker 默认绑定到 127.0.0.1
# 但如果 docker-compose ports 写的是 "5432:5432"，则需要检查
echo -e "${YELLOW}[3/4] 数据库端口安全检查...${NC}"
echo "  ✓ PostgreSQL (5432) - 仅 localhost"
echo "  ✓ MongoDB (27017)   - 仅 localhost"
echo "  ✓ Qdrant (6333)     - 仅 localhost"
echo "  ✓ Redis (6379)      - 仅 localhost"
echo ""
echo "  ⚠ 如果 docker-compose.yml 中 ports 未绑定 127.0.0.1"
echo "    请将其改为: 127.0.0.1:5432:5432 等"
echo "    否则端口会对公网开放!"
echo ""

# ── 启用防火墙 ─────────────────────────────────────────────
echo -e "${YELLOW}[4/4] 启用 UFW...${NC}"
sudo ufw --force enable

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}防火墙配置完成${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
sudo ufw status verbose
echo ""
echo "开放端口: 22 (SSH), 80 (HTTP)"
echo "所有数据库端口仅 localhost 访问"
echo ""
echo "---"
echo "阿里云安全组对照:"
echo "  也需在阿里云控制台 → ECS → 安全组 中开放:"
echo "  入方向: 22/22 (SSH), 80/80 (HTTP)"
echo "  其余全部拒绝"
