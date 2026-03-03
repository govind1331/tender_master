#!/bin/bash
# Full clean rebuild — use this whenever worker code changes
set -e

echo "==> Stopping all containers..."
docker-compose down --remove-orphans

echo "==> Removing stale images (forces full rebuild)..."
docker-compose build --no-cache

echo "==> Clearing stale .pyc files from volumes (if any)..."
find . -type f -name "*.pyc" -delete
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

echo "==> Starting all services..."
docker-compose up -d

echo "==> Waiting for services to be healthy..."
sleep 5
docker-compose ps

echo ""
echo "==> Worker logs (last 20 lines per worker):"
docker-compose logs --tail=20 worker-agents
docker-compose logs --tail=20 worker-orchestration
