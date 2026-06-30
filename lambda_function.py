import json
import re
import os
import boto3
from botocore.exceptions import ClientError

BUCKET = "emeraldbride-site"
CLOUDFRONT_BASE = "https://d27ppwg0xrr4g5.cloudfront.net"
ALLOWED_ORIGINS = {
    "https://d27ppwg0xrr4g5.cloudfront.net",
}

CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}


def get_cors_origin(event):
    origin = (event.get("headers") or {}).get("origin", "")
    if origin in ALLOWED_ORIGINS:
        return origin
    # Allow any localhost origin for local admin testing
    if re.match(r"^http://localhost(:\d+)?$", origin):
        return origin
    return list(ALLOWED_ORIGINS)[0]


def sanitize_filename(raw: str) -> str:
    name = raw.lower()
    name = name.replace(" ", "-")
    # Strip path traversal and disallowed characters; keep alphanum, dash, underscore, dot
    name = re.sub(r"[^a-z0-9._-]", "", name)
    # Collapse multiple dots to prevent extension spoofing like name..php.jpg
    name = re.sub(r"\.{2,}", ".", name)
    return name.strip("-.")


def lambda_handler(event, context):
    cors_origin = get_cors_origin(event)
    cors_headers = {
        "Access-Control-Allow-Origin": cors_origin,
        "Access-Control-Allow-Methods": "GET,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return {"statusCode": 200, "headers": cors_headers, "body": ""}

    params = event.get("queryStringParameters") or {}
    raw_filename = params.get("filename", "").strip()

    if not raw_filename:
        return {
            "statusCode": 400,
            "headers": cors_headers,
            "body": json.dumps({"error": "Missing required query parameter: filename"}),
        }

    filename = sanitize_filename(raw_filename)
    if not filename:
        return {
            "statusCode": 400,
            "headers": cors_headers,
            "body": json.dumps({"error": "Invalid filename"}),
        }

    ext = os.path.splitext(filename)[1].lower()
    content_type = CONTENT_TYPES.get(ext, "application/octet-stream")

    s3_key = f"images/gallery/{filename}"

    s3 = boto3.client("s3", region_name="us-east-1")
    try:
        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": BUCKET,
                "Key": s3_key,
                "ContentType": content_type,
            },
            ExpiresIn=300,
        )
    except ClientError as e:
        return {
            "statusCode": 500,
            "headers": cors_headers,
            "body": json.dumps({"error": str(e)}),
        }

    public_url = f"{CLOUDFRONT_BASE}/{s3_key}"

    return {
        "statusCode": 200,
        "headers": {**cors_headers, "Content-Type": "application/json"},
        "body": json.dumps({"uploadUrl": upload_url, "publicUrl": public_url}),
    }
