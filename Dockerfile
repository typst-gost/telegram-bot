# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

ARG TYPST_VERSION=0.15.1
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        xz-utils \
        fontconfig \
        fonts-liberation \
        fonts-dejavu-core \
        fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем официальный статический musl-бинарник Typst
RUN set -eux; \
    arch="${TARGETARCH:-$(uname -m)}"; \
    case "$arch" in \
        amd64|x86_64) typst_arch="x86_64" ;; \
        arm64|aarch64) typst_arch="aarch64" ;; \
        *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    archive="typst-${typst_arch}-unknown-linux-musl.tar.xz"; \
    url="https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/${archive}"; \
    curl -fsSL "$url" -o "/tmp/${archive}"; \
    tar -xJf "/tmp/${archive}" -C /tmp; \
    install -m 0755 "/tmp/typst-${typst_arch}-unknown-linux-musl/typst" /usr/local/bin/typst; \
    rm -rf "/tmp/${archive}" "/tmp/typst-${typst_arch}-unknown-linux-musl"; \
    typst --version; \
    fc-cache -f

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Сначала ставим зависимости из pyproject.toml для кэширования слоя Docker
COPY pyproject.toml README.md ./
RUN uv pip install --system --no-cache -r pyproject.toml

# Затем копируем исходники и ставим сам пакет (hatchling читает README.md)
COPY src ./src
RUN uv pip install --system --no-cache --no-deps .

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && groupadd --gid 1000 botuser \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin botuser \
    && mkdir -p /app/data /app/fonts \
    && chown -R botuser:botuser /app

USER botuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import pathlib, shutil, sys; cmd = pathlib.Path('/proc/1/cmdline').read_bytes(); sys.exit(0 if b'typst-gost-bot' in cmd and shutil.which('typst') else 1)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["typst-gost-bot"]