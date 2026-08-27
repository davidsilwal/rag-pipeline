FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace

WORKDIR /workspace

COPY worker-requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    pip install --no-cache-dir mcp[cli] sentence-transformers

COPY . /workspace

EXPOSE 8080

CMD ["python", "mcp_server/server.py", "--transport", "sse", "--port", "8080"]
