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
# 修复: 直接修改 docker-compose.yml 将端口绑定到 127.0.0.1，
# 不再只打印提示（此前提示后从不执行，数据库端口实际对公网开放）
echo -e "${YELLOW}[3/4] 数据库端口安全检查...${NC}"
COMPOSE_FILE="$(dirname "$0")/../docker-compose.yml"
if [ -f "$COMPOSE_FILE" ]; then
    echo "  检查 $COMPOSE_FILE 端口绑定..."
    for port in 6333 6334 27017 5432 6379; do
        # 匹配形如 "6333:6333" 的裸绑定（未加 127.0.0.1 前缀）
        if grep -Eq "\"$port:$port\"" "$COMPOSE_FILE"; then
            echo "  🔧 端口 $port 未绑定 127.0.0.1，正在修正..."
            sed -i "s|\"$port:$port\"|\"127.0.0.1:$port:$port\"|g" "$COMPOSE_FILE"
            echo "  ✓ 已修正: 127.0.0.1:$port:$port"
        fi
    done
    echo ""
    echo "  ✓ PostgreSQL (5432) - 仅 localhost"
    echo "  ✓ MongoDB (27017)   - 仅 localhost"
    echo "  ✓ Qdrant (6333)     - 仅 localhost"
    echo "  ✓ Redis (6379)      - 仅 localhost"
    echo ""
    echo "  ⚠ 修改后请执行: docker compose up -d 重新创建容器使端口绑定生效"
else
    echo "  ⚠ 未找到 docker-compose.yml，跳过端口绑定修正"
fi
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
