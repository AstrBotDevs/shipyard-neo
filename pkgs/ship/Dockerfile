# ── Proxy support ──────────────────────────────────────
# Pass via --build-arg HTTP_PROXY=... HTTPS_PROXY=... NO_PROXY=...
# NOTE: before first FROM only ARG is allowed (no ENV).
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG no_proxy

# ===== 构建阶段 =====
FROM python:3.13-slim-bookworm AS builder

# ── Proxy (must re-declare ARG in each FROM) ───────────
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG no_proxy
ENV HTTP_PROXY=${HTTP_PROXY} HTTPS_PROXY=${HTTPS_PROXY} NO_PROXY=${NO_PROXY}
ENV http_proxy=${http_proxy} https_proxy=${https_proxy} no_proxy=${no_proxy}
# apt doesn't honour *_proxy env vars; configure it explicitly
RUN _http_proxy="${HTTP_PROXY:-${http_proxy:-}}"; \
    _https_proxy="${HTTPS_PROXY:-${https_proxy:-}}"; \
    if [ -n "${_http_proxy}" ]; then \
      echo "Acquire::http::Proxy \"${_http_proxy}\";"   > /etc/apt/apt.conf.d/99proxy ; \
      echo "Acquire::https::Proxy \"${_https_proxy}\";" >> /etc/apt/apt.conf.d/99proxy ; \
    fi

WORKDIR /build

# 安装构建依赖（临时）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    python3-dev \
    libffi-dev \
    libpng-dev \
    libjpeg-dev \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# 使用 uv 安装 Python 依赖到指定目录
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
COPY requirements.txt .

# 安装依赖，禁用缓存
RUN uv pip install --no-cache --prefix=/install -r requirements.txt \
    # 删除不需要的文件以减小体积
    && find /install -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true \
    && find /install -name '*.pyc' -delete 2>/dev/null || true \
    && find /install -name '*.pyo' -delete 2>/dev/null || true

# ===== 最终镜像 =====
FROM python:3.13-slim-bookworm

# ── Proxy: ARG only, export in RUN, no ENV — clean runtime ──
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG no_proxy

WORKDIR /app

# 单个 RUN 命令安装所有运行时依赖和配置用户，减少镜像层数
# 代理通过 export 注入，用完即弃，不残留到运行时镜像
RUN export HTTP_PROXY="${HTTP_PROXY}" HTTPS_PROXY="${HTTPS_PROXY}" \
           http_proxy="${http_proxy}" https_proxy="${https_proxy}" \
           NO_PROXY="${NO_PROXY}" no_proxy="${no_proxy}" && \
    _p="${HTTP_PROXY:-${http_proxy:-}}"; \
    _sp="${HTTPS_PROXY:-${https_proxy:-}}"; \
    if [ -n "${_p}" ]; then \
      echo "Acquire::http::Proxy \"${_p}\";"   > /etc/apt/apt.conf.d/99proxy ; \
      echo "Acquire::https::Proxy \"${_sp}\";" >> /etc/apt/apt.conf.d/99proxy ; \
    fi && \
    apt-get update && apt-get install -y --no-install-recommends \
    # 图像处理运行时库（Pillow / OpenCV headless）
    libpng16-16 \
    libjpeg62-turbo \
    libglib2.0-0 \
    # lxml 运行时库
    libxml2 \
    libxslt1.1 \
    # 字体显示
    fontconfig \
    fonts-noto-cjk \
    fonts-symbola \
    # 用户管理工具
    sudo \
    # Node.js 安装需要的工具
    curl \
    gnupg \
    git \
    # 压缩/解压工具
    zip \
    unzip \
    # 调试工具（可选，生产环境可移除）
    vim-tiny \
    nano \
    less \
    procps \
    htop \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/apt/apt.conf.d/99proxy \
    # 创建用户和目录
    && groupadd -g 1000 shipyard \
    && useradd -u 1000 -g shipyard -d /workspace -s /bin/bash shipyard \
    && mkdir -p /workspace \
    && chown shipyard:shipyard /workspace \
    && chmod 755 /workspace \
    # 配置 sudo
    && echo "root ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers \
    && echo "Defaults:root !requiretty" >> /etc/sudoers \
    && echo "shipyard ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers \
    # 创建 .bashrc
    && echo '# Basic shell configuration for shipyard user' > /workspace/.bashrc \
    && echo "export PS1='\\u@shipyard:\\w\\$ '" >> /workspace/.bashrc \
    && echo 'export PATH=/usr/local/bin:/usr/bin:/bin' >> /workspace/.bashrc \
    && echo 'export HOME=/workspace' >> /workspace/.bashrc \
    && echo 'cd /workspace' >> /workspace/.bashrc \
    && chown shipyard:shipyard /workspace/.bashrc \
    && chmod 644 /workspace/.bashrc

# 从 builder 阶段复制 Python 包
COPY --from=builder /install /usr/local

# 安装 Node.js (LTS) - 代理通过 export，用完即弃
RUN export HTTP_PROXY="${HTTP_PROXY}" HTTPS_PROXY="${HTTPS_PROXY}" \
           http_proxy="${http_proxy}" https_proxy="${https_proxy}" \
           NO_PROXY="${NO_PROXY}" no_proxy="${no_proxy}" && \
    _p="${HTTP_PROXY:-${http_proxy:-}}"; \
    _sp="${HTTPS_PROXY:-${https_proxy:-}}"; \
    if [ -n "${_p}" ]; then \
      echo "Acquire::http::Proxy \"${_p}\";"   > /etc/apt/apt.conf.d/99proxy ; \
      echo "Acquire::https::Proxy \"${_sp}\";" >> /etc/apt/apt.conf.d/99proxy ; \
    fi && \
    curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/apt/apt.conf.d/99proxy \
    && npm install -g pnpm vercel --no-audit --no-fund \
    && npm cache clean --force \
    && rm -rf /root/.npm

# 复制项目文件（依赖 .dockerignore 排除不需要的文件）
COPY . .

# 设置入口脚本权限
RUN chmod +x /app/entrypoint.sh

# 预热 matplotlib 字体缓存（构建时完成，运行时跳过）
# 这可以节省每次容器启动时约 2-3 秒的字体缓存重建时间
RUN python <<'EOF'
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# 构建字体缓存
fm._load_fontmanager(try_read_cache=False)

# 配置中文字体
font_candidates = ['Noto Sans CJK SC', 'Noto Sans CJK JP', 'Noto Sans CJK TC']

# 检查 Symbola 字体是否可用
symbola_available = any('Symbola' in f.name for f in fm.fontManager.ttflist)
if symbola_available:
    font_candidates.append('Symbola')

font_candidates.append('DejaVu Sans')

plt.rcParams['font.sans-serif'] = font_candidates
plt.rcParams['axes.unicode_minus'] = False

# 创建一个图来触发完整的初始化
fig, ax = plt.subplots()
ax.set_title('Font cache warmup')
plt.close(fig)

print('Matplotlib font cache warmed up successfully')

# Compile the fixed kernel startup dependencies at their final runtime paths.
# Python still validates these caches against the source files before using
# them. Keep workspace files and kernel execution out of the image build.
import compileall
import py_compile
import sysconfig
from pathlib import Path

site_packages = Path(sysconfig.get_path('purelib'))
for package in ('jupyter_client', 'jupyter_core', 'ipykernel', 'IPython', 'traitlets', 'zmq'):
    source_dir = site_packages / package
    if not source_dir.is_dir() or not compileall.compile_dir(
        source_dir,
        quiet=1,
        force=True,
        invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
    ):
        raise RuntimeError(f'Failed to precompile kernel dependency: {package}')
print('Kernel startup bytecode cache prepared successfully')
EOF

# 暴露端口
EXPOSE 8123

# 入口脚本：修复 workspace 权限后启动应用
ENTRYPOINT ["/app/entrypoint.sh"]

# 启动命令（以 root 运行 FastAPI，内部通过 sudo 切换到 shipyard 执行命令）
CMD ["python", "run.py"]
