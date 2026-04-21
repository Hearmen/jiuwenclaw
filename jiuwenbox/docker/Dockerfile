FROM openeuler/openeuler:24.03

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN echo "sslverify=false" >> /etc/yum.conf
RUN echo "insecure" >> ~/.curlrc
RUN sed -i 's/gpgcheck=1/gpgcheck=0/g' /etc/yum.conf /etc/yum.repos.d/openEuler.repo

RUN yum makecache \
    && yum install -y \
        bubblewrap \
        iproute \
        iptables \
        iptables-nft \
        nftables \
        ca-certificates \
        nodejs \
        python3-pip \
        git \
        vim \
        unzip

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs
COPY scripts ./scripts

RUN chmod +x ./scripts/server.sh

RUN pip install --break-system-packages -e . -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip install --break-system-packages -i https://pypi.tuna.tsinghua.edu.cn/simple \
        "fastapi>=0.104.0" \
        "uvicorn>=0.24.0" \
        "pydantic>=2.5.0" \
        "pydantic-settings>=2.1.0" \
        "sqlalchemy>=2.0.0" \
        "async-gaussdb>=0.30.4" \
        "aiomysql>=0.2.0" \
        "aiosqlite>=0.19.0" \
        "python-dotenv>=1.0.0" \
        "apscheduler>=3.10.0" \
        "loguru>=0.7.0" \
        "httpx>=0.25.0" \
        "pyyaml>=6.0.0" \
        "greenlet>=3.3.0" \
        "openjiuwen==0.1.7" \
        "jinja2>=3.0.0" \
        "mcp>=1.0.0" \
        "sse-starlette>=1.6.0" \
        numexpr \
        "redis~=7.3.0" \
        "opencv-python>=4.10.0"

EXPOSE 8321

CMD ["./scripts/server.sh"]
