FROM mambaorg/micromamba:latest

ARG MAMBA_DOCKERFILE_ACTIVATE=1
WORKDIR /app

COPY --chown=$MAMBA_USER:$MAMBA_USER environment.runtime.yml /tmp/environment.yml
RUN micromamba install --yes --name base --file /tmp/environment.yml \
    && micromamba clean --all --yes

COPY --chown=$MAMBA_USER:$MAMBA_USER . /app
RUN python -m pip install --no-build-isolation --no-deps --editable .

EXPOSE 8000
VOLUME ["/app/generated"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
