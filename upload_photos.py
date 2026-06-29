#!/usr/bin/env python3
import os
import sys
from pathlib import Path

try:
    import boto3
except ImportError:
    print("pip install boto3")
    sys.exit(1)

SOURCE_DIR = Path("/Users/d.madera/Desktop/Programs/emeraldbride.com/processed")
BUCKET_NAME = "emeraldbride-site"
PREFIX = "images/gallery/"
PROFILE_NAME = "emeraldbride"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def get_content_type(file_path: Path) -> str:
    return MIME_TYPES.get(file_path.suffix.lower(), "application/octet-stream")


def main() -> None:
    if not SOURCE_DIR.exists():
        print(f"Source directory not found: {SOURCE_DIR}")
        return

    session = boto3.Session(profile_name=PROFILE_NAME)
    s3 = session.client("s3")

    uploaded_files = 0
    total_size_bytes = 0

    for file_path in sorted(SOURCE_DIR.iterdir()):
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue

        try:
            file_size = file_path.stat().st_size
            key = f"{PREFIX}{file_path.name}"
            content_type = get_content_type(file_path)

            s3.upload_file(
                str(file_path),
                BUCKET_NAME,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "CacheControl": "max-age=31536000",
                },
            )

            uploaded_files += 1
            total_size_bytes += file_size
            print(f"{file_path.name} -> s3://{BUCKET_NAME}/{key}")
        except Exception as exc:
            print(f"Error uploading {file_path.name}: {exc}")
            continue

    total_size_mb = total_size_bytes / (1024 * 1024)
    print(f"Total files uploaded: {uploaded_files}")
    print(f"Total size uploaded (MB): {total_size_mb:.2f}")


if __name__ == "__main__":
    main()
