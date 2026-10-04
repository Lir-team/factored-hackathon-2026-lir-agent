# Data pipeline as a Cloud Run job: S3 -> raw -> staging -> quality -> curated -> insights,
# then staging/ and curated/ are published to the mounted data lake bucket (LAKE_DIR).
#
# Build from data/:  docker build -t lir-pipeline data/

ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg

RUN useradd --create-home --uid 10001 lir
WORKDIR /app/data
COPY requirements-pipeline.txt ./
RUN pip install -r requirements-pipeline.txt

# Code, contracts, glossary and manifests; raw data and outputs are never baked in.
COPY --chown=lir:lir . ./
# Build contexts uploaded from Windows can arrive without write bits: the pipeline writes
# its manifests, raw/ and outputs here, so the owner gets read-write on everything.
RUN mkdir -p raw staging curated reports \
    && chown -R lir:lir /app/data \
    && chmod -R u+rwX /app/data
USER lir

CMD ["python", "-m", "job.run"]
