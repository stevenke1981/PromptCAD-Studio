#!/usr/bin/env sh
set -eu
[ -f .env ] || cp .env.example .env
docker compose up --build
