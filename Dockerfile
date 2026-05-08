FROM python:3.14-alpine

ARG KUSTOMIZE_VERSION=v5.4.3
ARG GATOR_VERSION=v3.17.0
RUN apk add --no-cache curl && \
    curl -LO "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2F${KUSTOMIZE_VERSION}/kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" && \
    tar -xzf kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz && \
    mv kustomize /usr/local/bin && \
    rm kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz && \
    curl -LO "https://github.com/open-policy-agent/gatekeeper/releases/download/${GATOR_VERSION}/gator-${GATOR_VERSION}-linux-amd64.tar.gz" && \
    tar -xzf gator-${GATOR_VERSION}-linux-amd64.tar.gz && \
    mv gator /usr/local/bin && \
    rm gator-${GATOR_VERSION}-linux-amd64.tar.gz && \
    apk del curl

ENV UV_LINK_MODE=copy
WORKDIR /app

# Install dependencies
RUN --mount=from=ghcr.io/astral-sh/uv:alpine,source=/usr/local/bin/uv,target=/bin/uv \
# RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev --verbose


COPY src /app/src/
COPY README.md .

RUN --mount=from=ghcr.io/astral-sh/uv:alpine,source=/usr/local/bin/uv,target=/bin/uv \
# RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-editable --no-dev --verbose

    ENTRYPOINT [ "/app/.venv/bin/hydrate" ]
