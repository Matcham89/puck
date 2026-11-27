FROM python:3.12-alpine
ARG TARGETARCH
RUN case "$TARGETARCH" in amd64|arm64) ;; *) echo "unsupported TARGETARCH '$TARGETARCH' (use docker buildx, or build for amd64/arm64)" >&2; exit 1;; esac
RUN apk add --no-cache git jq bash curl tree ca-certificates py3-yaml unzip yamllint shellcheck helm && pip install --no-cache-dir redis jinja2
# Deterministic lint toolchain: pinned versions, sha256-verified per architecture (from each vendor's published checksum file).
ARG TF_VERSION=1.17.2
ARG TF_SHA256_amd64=e6f16b2f0b137ee5729caa8c4e79a3bae4ee261acd198a1fcfa8584a6296605b
ARG TF_SHA256_arm64=858e08c181a76b6b223e8c767a8e983922eebe2d062db949a2de66ce040c368c
ARG TFLINT_VERSION=v0.66.0
ARG TFLINT_SHA256_amd64=22be11cadc25cd02401e94c0ccc91aa63467debb8485c2a755d98f4c0a9d1e2e
ARG TFLINT_SHA256_arm64=1105a0a5ddee7cdac7957a3475571a946b839192ea1bc0c722c6c4686001067e
ARG KUBECONFORM_VERSION=v0.8.0
ARG KUBECONFORM_SHA256_amd64=9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
ARG KUBECONFORM_SHA256_arm64=1f53fc8e81258197a35e8603054162a5af1de8c5af13746c71ab680d9534ed87
RUN sum() { eval "echo \$$1_$TARGETARCH"; } \
 && curl -fsSL -o /tmp/tf.zip https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_${TARGETARCH}.zip \
 && echo "$(sum TF_SHA256)  /tmp/tf.zip" | sha256sum -c - && unzip -q /tmp/tf.zip terraform -d /usr/local/bin && rm /tmp/tf.zip \
 && curl -fsSL -o /tmp/tflint.zip https://github.com/terraform-linters/tflint/releases/download/${TFLINT_VERSION}/tflint_linux_${TARGETARCH}.zip \
 && echo "$(sum TFLINT_SHA256)  /tmp/tflint.zip" | sha256sum -c - && unzip -q /tmp/tflint.zip tflint -d /usr/local/bin && rm /tmp/tflint.zip \
 && curl -fsSL -o /tmp/kc.tgz https://github.com/yannh/kubeconform/releases/download/${KUBECONFORM_VERSION}/kubeconform-linux-${TARGETARCH}.tar.gz \
 && echo "$(sum KUBECONFORM_SHA256)  /tmp/kc.tgz" | sha256sum -c - && tar xzf /tmp/kc.tgz -C /usr/local/bin kubeconform && rm /tmp/kc.tgz
# Same skill validator the agent's own image uses, from the pinned official commit.
RUN git init -q /tmp/o && git -C /tmp/o fetch -q --depth 1 https://github.com/anthropics/claude-plugins-official a2e251d9b97b909bf728b3e84fc8d17e6aba0063 \
 && git -C /tmp/o checkout -q FETCH_HEAD && cp /tmp/o/plugins/skill-creator/skills/skill-creator/scripts/quick_validate.py /opt/quick_validate.py && rm -rf /tmp/o
RUN pip install --no-cache-dir pyyaml
COPY application/playbook-check.sh /usr/local/bin/playbook-check.sh
COPY application/eda.py application/worker.py application/lint.py application/ui.py application/jira.py application/linear.py /app/
COPY application/templates /app/templates
RUN chmod +x /usr/local/bin/playbook-check.sh && adduser -D -u 10001 app && mkdir -p /data && chown app /data
USER app
WORKDIR /app
ENTRYPOINT ["python3", "/app/eda.py"]
