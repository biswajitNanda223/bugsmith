# Image the patch-validation sandbox runs tests in when BUGSMITH_SANDBOX=docker.
# Build:  mise run sandbox:build      (or: docker build -t bugsmith-sandbox sandbox)
# Add whatever the repo under test needs to import; the demo app needs fastapi.
FROM python:3.13-slim
RUN pip install --no-cache-dir pytest fastapi httpx
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox
USER 10001
WORKDIR /work
