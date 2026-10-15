# 最小运行环境。引擎本身只用标准库，所以镜像很干净。
#
#   docker build -t target-audit .
#   docker run --rm target-audit audit KRAS --cancer LUAD --mock
#   docker run --rm target-audit self-test
#
# 接真实数据时需要联网；离线只跑规则与解释层兜底的话，加 --mock 即可。
FROM python:3.12-slim

WORKDIR /app

# 引擎只用标准库，不需要 pip install。
# 这里显式声明版本要求，避免有人误用旧解释器。
RUN python -c "import sys; assert sys.version_info >= (3, 10), '需要 Python 3.10+'"

COPY tsa/ ./tsa/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY run.py README.md LICENSE ./

# 参考数据（gnomAD 约束表、DepMap 矩阵）不打包进镜像：
# 前者可用 scripts/fetch_reference_data.py 取，后者约 400MB 且需要手动下载。
# 挂载到 /app/data 即可复用宿主机上已经下好的数据。
VOLUME ["/app/data"]

ENTRYPOINT ["python", "run.py"]
CMD ["self-test"]
