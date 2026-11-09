FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py config.py engine.py indicators.py strategy.py state.py notifier.py dashboard.py ./
COPY dashboard_ui ./dashboard_ui
RUN mkdir -p /app/data
EXPOSE 8765
CMD ["python", "bot.py"]
