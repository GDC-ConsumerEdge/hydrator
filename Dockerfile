FROM --platform=linux/amd64 python:3.12-alpine

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

ADD . /app
WORKDIR /app
RUN pip3 install -r requirements-setuptools.txt --require-hashes --no-cache-dir && \
    pip3 install -r requirements.txt --require-hashes --no-cache-dir && \
    pip3 install --no-deps --no-index --no-build-isolation .

ENTRYPOINT [ "hydrate" ]
