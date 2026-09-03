name: RSI Getiri Analizi

on:
  workflow_dispatch: {}

jobs:
  run-analysis:
    runs-on: ubuntu-latest
    steps:
      - name: Repoyu çek
        uses: actions/checkout@v4

      - name: Python kur
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Bağımlılıkları kur
        run: pip install requests

      - name: Getiri analizini çalıştır
        run: python analyze_returns.py
