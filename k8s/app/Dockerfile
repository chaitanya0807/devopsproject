FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir flask==3.1.2 prometheus-client==0.23.1 gunicorn==23.0.0
COPY app.py .
EXPOSE 8080
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "1", "--threads", "4", "app:app"]
