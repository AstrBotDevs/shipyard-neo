.PHONY: help install test test-integration test-unit clean build run

# 默认目标
help:
	@echo "Available targets:"
	@echo "  help              - Show this help message"
	@echo "  test-integration  - Run integration tests only"
	@echo "  clean             - Clean up Docker containers and images"
	@echo "  build             - Build Docker image"
	@echo "  run               - Run the ship container"

# 运行集成测试
test-integration:
	python -m pytest tests/integration/ -v -m integration

# 清理Docker资源
clean:
	@echo "Cleaning up Docker resources..."
	-docker container prune -f
	-docker image rm soulter/shipyard-ship:test 2>/dev/null || true
	-docker system prune -f

# 构建Docker镜像
build:
	docker build -t soulter/shipyard-ship:latest .

# 运行Ship容器
run: build
	docker run --rm -p 8123:8123 soulter/shipyard-ship:latest

# 开发模式运行（本地）
dev:
	python run.py
