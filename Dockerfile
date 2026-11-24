# ipv64-sites — doctor + student + teacher in one container (Cloady free tier)
# Pure stdlib Python; no dependencies to install, so the build is just a copy.
FROM python:3.12-slim
WORKDIR /app
COPY app.py .
COPY static/ ./static/
# Cloady routes to the port named by the PORT variable (default 3000).
# The app binds $PORT, falling back to $SERVER_PORT, then 8080.
ENV PORT=8080
EXPOSE 8080
CMD ["python3", "app.py"]
