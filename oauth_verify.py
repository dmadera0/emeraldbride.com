import json

from auth_lib import require_auth

ALLOWED_ORIGINS = {
    "https://d27ppwg0xrr4g5.cloudfront.net",
    "http://localhost:3000",
    "http://localhost:5500",
    "http://localhost:8000",
    "http://127.0.0.1:5500",
}


def get_cors_origin(event):
    origin = (event.get("headers") or {}).get("origin", "")
    if origin in ALLOWED_ORIGINS:
        return origin
    return "https://d27ppwg0xrr4g5.cloudfront.net"


def lambda_handler(event, context):
    cors_headers = {
        "Access-Control-Allow-Origin": get_cors_origin(event),
        "Access-Control-Allow-Methods": "GET,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Credentials": "true",
    }

    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return {"statusCode": 200, "headers": cors_headers, "body": ""}

    claims = require_auth(event)
    if not claims:
        return {
            "statusCode": 200,
            "headers": {**cors_headers, "Content-Type": "application/json"},
            "body": json.dumps({"authenticated": False}),
        }

    return {
        "statusCode": 200,
        "headers": {**cors_headers, "Content-Type": "application/json"},
        "body": json.dumps({"authenticated": True, "email": claims.get("email")}),
    }
