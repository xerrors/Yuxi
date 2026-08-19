# https://gcore.jsdelivr.net/gh/opendatalab/MinerU@master/docker/china/Dockerfile
# Use DaoCloud mirrored vllm image for China region for gpu with Volta、Turing、Ampere、Ada Lovelace、Hopper、Blackwell architecture (7.0 <= Compute Capability <= 12.0)
# Compute Capability version query (https://developer.nvidia.com/cuda-gpus)
# support x86_64 architecture and ARM(AArch64) architecture
FROM docker.m.daocloud.io/vllm/vllm-openai:v0.27.1

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
RUN python3 -m pip install -U 'mineru[core]==3.4.4' -i https://mirrors.aliyun.com/pypi/simple --break-system-packages && \
    python3 -m pip cache purge

# Download models and update the configuration file
RUN /bin/bash -c "mineru-models-download -s modelscope -m all"

# Set the entry point to activate the virtual environment and run the command line tool
ENTRYPOINT ["/bin/bash", "-c", "export MINERU_MODEL_SOURCE=local && exec \"$@\"", "--"]