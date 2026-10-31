# https://gcore.jsdelivr.net/gh/opendatalab/MinerU@master/docker/china/Dockerfile
# Use DaoCloud mirrored vllm image for China region for gpu with Volta、Turing、Ampere、Ada Lovelace、Hopper、Blackwell architecture (7.0 <= Compute Capability <= 12.0)
# Compute Capability version query (https://developer.nvidia.com/cuda-gpus)
# support x86_64 architecture and ARM(AArch64) architecture
FROM docker.m.daocloud.io/vllm/vllm-openai:v0.21.0

# Install libgl for opencv support & Noto fonts for Chinese characters
RUN apt-get update && \
    apt-get install -y \
        fonts-noto-core \
        fonts-noto-cjk \
        fontconfig \
        libgl1 && \
    fc-cache -fv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install mineru with pinned version.
# MinerU 许可证随版本变化：3.0.x = AGPL-3.0；3.1.0+ = Apache-2.0 + 附加条款
# （MAU>1 亿或月收入>$2000 万需商业许可，在线服务须标注归属）。锁版本保证构建产物许可可预期。
RUN python3 -m pip install -U 'mineru[torch]==4.1.3' -i https://mirrors.aliyun.com/pypi/simple --break-system-packages && \
    python3 -m pip cache purge

# 构建机与运行机显式使用相同后端，避免无 GPU 构建下载错误的模型包。
ENV MINERU_MODEL_SMALL_BACKEND=torch \
    MINERU_MODEL_VLM_ENGINE=vllm
RUN mineru-kit models download --tier standard --small-backend torch --vlm-engine vllm --source modelscope && \
    mineru-kit models verify --tier standard --small-backend torch --vlm-engine vllm

# Set the entry point to activate the virtual environment and run the command line tool
ENTRYPOINT ["/bin/bash", "-c", "export MINERU_MODEL_SOURCE=local && exec \"$@\"", "--"]
