FROM python:3.13-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libarchive-tools 7zip ca-certificates \
      syslinux syslinux-common syslinux-utils mtools dosfstools wimtools fdisk \
 && rm -rf /var/lib/apt/lists/* \
 && (command -v 7z || ln -s "$(command -v 7zz)" /usr/local/bin/7z)
COPY app.py /app/app.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python3", "/app/app.py"]
