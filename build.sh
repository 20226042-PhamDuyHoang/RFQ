#!/usr/bin/env bash
# Build script cho Render.com
# Cai dat system dependencies (Tesseract OCR, Poppler cho pdf2image) truoc khi pip install

set -e

echo "==> Installing system dependencies..."
apt-get update -qq && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

echo "==> Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Build complete."
