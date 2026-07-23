"""
deploy_upload_lambda.py

Sets up the serverless endpoints for the emeraldbride site:
  1. GET  /presign-upload      → emeraldbride-presign-upload      (auth required)
  2. POST /save-gallery-state  → emeraldbride-save-gallery-state  (auth required)
  3. POST /save-hero-state     → emeraldbride-save-hero-state     (auth required)
  4. GET  /list-gallery        → emeraldbride-list-gallery        (auth required)
  5. POST /oauth-callback      → emeraldbride-oauth-callback      (public — issues session)
  6. GET  /oauth-verify        → emeraldbride-oauth-verify        (public — checks session)
  7. POST /oauth-logout        → emeraldbride-oauth-logout        (public — clears session)

All functions share the IAM role emeraldbride-lambda-role and the API Gateway
HTTP API emeraldbride-api (ID: sg0k9b4ggd), prod stage. Functions 1-4 and 5-7
all bundle auth_lib.py (this repo has no dependency/layer packaging, so each
zip carries its own copy) and share a JWT_SECRET env var so tokens signed by
oauth-callback verify correctly everywhere else.

Run with:  python deploy_upload_lambda.py
Requires:
  - AWS CLI profile 'emeraldbride' configured (region us-east-1)
  - Environment variables (see README for how to get these):
      EB_GOOGLE_CLIENT_ID       (required — from Google Cloud Console)
      EB_GOOGLE_CLIENT_SECRET   (required — from Google Cloud Console)
      EB_ALLOWED_EMAILS         (comma-separated exact Google account emails)
      EB_ALLOWED_DOMAINS        (comma-separated Google Workspace domains, optional)
      EB_GOOGLE_REDIRECT_URI    (optional, defaults to the CloudFront admin.html URL)
      EB_JWT_SECRET             (optional — auto-generated and printed once if unset;
                                  save it and reuse it on future deploys, otherwise
                                  every redeploy invalidates all existing sessions)
    At least one of EB_ALLOWED_EMAILS / EB_ALLOWED_DOMAINS must be set, or nobody
    will be able to log in.
"""

import io
import json
import os
import secrets
import sys
import time
import zipfile
import boto3
from botocore.exceptions import ClientError

PROFILE  = "emeraldbride"
REGION   = "us-east-1"
ROLE_NAME = "emeraldbride-lambda-role"
ROLE_ARN  = "arn:aws:iam::078232195170:role/emeraldbride-lambda-role"
BUCKET   = "emeraldbride-site"
API_NAME = "emeraldbride-api"
API_ID   = "sg0k9b4ggd"
STAGE_NAME = "prod"
CLOUDFRONT_BASE = "https://d27ppwg0xrr4g5.cloudfront.net"

AUTH_LIB_FILE = "auth_lib.py"

CORS_ALLOW_ORIGINS = [
    CLOUDFRONT_BASE,
    "http://localhost:3000",
    "http://localhost:5500",
    "http://localhost:8000",
    "http://127.0.0.1:5500",
]

session    = boto3.Session(profile_name=PROFILE, region_name=REGION)
iam        = session.client("iam")
aws_lambda = session.client("lambda")
apigw      = session.client("apigatewayv2")
sts        = session.client("sts")

ACCOUNT_ID = sts.get_caller_identity()["Account"]


# ---------------------------------------------------------------------------
# 0. Auth configuration — read from the local environment, never hardcoded
# ---------------------------------------------------------------------------

def load_auth_config() -> dict:
    google_client_id     = os.environ.get("EB_GOOGLE_CLIENT_ID", "").strip()
    google_client_secret = os.environ.get("EB_GOOGLE_CLIENT_SECRET", "").strip()
    google_redirect_uri  = os.environ.get("EB_GOOGLE_REDIRECT_URI", "").strip() or f"{CLOUDFRONT_BASE}/admin.html"
    allowed_emails        = os.environ.get("EB_ALLOWED_EMAILS", "").strip()
    allowed_domains        = os.environ.get("EB_ALLOWED_DOMAINS", "").strip()
    jwt_secret             = os.environ.get("EB_JWT_SECRET", "").strip()

    missing = []
    if not google_client_id:
        missing.append("EB_GOOGLE_CLIENT_ID")
    if not google_client_secret:
        missing.append("EB_GOOGLE_CLIENT_SECRET")
    if not allowed_emails and not allowed_domains:
        missing.append("EB_ALLOWED_EMAILS and/or EB_ALLOWED_DOMAINS")

    if missing:
        print("=" * 60)
        print(" Missing required auth configuration:")
        for m in missing:
            print(f"   - {m}")
        print(" Set these environment variables and re-run. See the module")
        print(" docstring at the top of this file for details.")
        print("=" * 60)
        sys.exit(1)

    if not jwt_secret:
        jwt_secret = secrets.token_hex(32)
        print("=" * 60)
        print(" No EB_JWT_SECRET was set — generated a new one:")
        print(f"   {jwt_secret}")
        print(" Save this somewhere safe (password manager) and export it as")
        print(" EB_JWT_SECRET on future deploys. If you lose it, that's fine —")
        print(" the next deploy just generates a new one and every existing")
        print(" admin session gets signed out.")
        print("=" * 60)

    return {
        "GOOGLE_CLIENT_ID": google_client_id,
        "GOOGLE_CLIENT_SECRET": google_client_secret,
        "GOOGLE_REDIRECT_URI": google_redirect_uri,
        "ALLOWED_EMAILS": allowed_emails,
        "ALLOWED_DOMAINS": allowed_domains,
        "JWT_SECRET": jwt_secret,
    }


# ---------------------------------------------------------------------------
# 1. IAM Role (inline policy covers all functions)
# ---------------------------------------------------------------------------

TRUST_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
})

S3_INLINE_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "s3:PutObject",
            "Resource": [
                f"arn:aws:s3:::{BUCKET}/images/gallery/*",
                f"arn:aws:s3:::{BUCKET}/gallery-state.json",
                f"arn:aws:s3:::{BUCKET}/hero-state.json",
            ],
        },
        {
            "Effect": "Allow",
            "Action": "s3:ListBucket",
            "Resource": f"arn:aws:s3:::{BUCKET}",
            "Condition": {"StringLike": {"s3:prefix": "images/gallery/*"}},
        },
    ],
})


def ensure_iam_role() -> str:
    print(f"[IAM] Ensuring role '{ROLE_NAME}' exists...")
    try:
        role = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=TRUST_POLICY,
            Description="Lambda execution role for emeraldbride API functions",
        )
        role_arn = role["Role"]["Arn"]
        print(f"[IAM] Created role: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print(f"[IAM] Role already exists: {role_arn}")

    try:
        iam.put_role_policy(
            RoleName=ROLE_NAME,
            PolicyName="s3-presign-gallery",
            PolicyDocument=S3_INLINE_POLICY,
        )
        print("[IAM] Inline S3 policy applied.")
    except ClientError as e:
        print(f"[IAM] Warning attaching inline policy: {e}")

    managed_policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
    try:
        iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn=managed_policy_arn)
        print("[IAM] AWSLambdaBasicExecutionRole attached.")
    except iam.exceptions.LimitExceededException:
        print("[IAM] AWSLambdaBasicExecutionRole already attached.")
    except ClientError as e:
        print(f"[IAM] Warning attaching managed policy: {e}")

    return role_arn


# ---------------------------------------------------------------------------
# 2. Package Lambda zips
# ---------------------------------------------------------------------------

def build_zip(*sources: str) -> bytes:
    """Zips one or more source files by their own basename. Every function
    that imports auth_lib passes it as a second source alongside its handler."""
    print(f"[ZIP] Packaging {', '.join(sources)}...")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for source_file in sources:
            zf.write(source_file, arcname=os.path.basename(source_file))
    buf.seek(0)
    data = buf.read()
    print(f"[ZIP] Package size: {len(data):,} bytes")
    return data


# ---------------------------------------------------------------------------
# 3. Lambda helpers
# ---------------------------------------------------------------------------

def _wait_active(function_name: str) -> None:
    for _ in range(10):
        state = aws_lambda.get_function(FunctionName=function_name)["Configuration"]["State"]
        if state == "Active":
            break
        print(f"[Lambda] {function_name} state: {state}, waiting...")
        time.sleep(3)


def ensure_lambda(
    function_name: str,
    handler: str,
    description: str,
    role_arn: str,
    zip_bytes: bytes,
    environment: dict = None,
) -> str:
    print(f"[Lambda] Ensuring function '{function_name}'...")
    config = {"Variables": environment} if environment else {}

    for attempt in range(6):
        try:
            fn = aws_lambda.create_function(
                FunctionName=function_name,
                Runtime="python3.12",
                Role=role_arn,
                Handler=handler,
                Code={"ZipFile": zip_bytes},
                Timeout=10,
                Description=description,
                Environment=config,
            )
            fn_arn = fn["FunctionArn"]
            print(f"[Lambda] Created {function_name}: {fn_arn}")
            break
        except aws_lambda.exceptions.ResourceConflictException:
            print(f"[Lambda] {function_name} already exists; updating code and config...")
            aws_lambda.update_function_code(FunctionName=function_name, ZipFile=zip_bytes)
            time.sleep(3)
            aws_lambda.update_function_configuration(
                FunctionName=function_name,
                Role=role_arn,
                Runtime="python3.12",
                Handler=handler,
                Timeout=10,
                Environment=config,
            )
            fn_arn = aws_lambda.get_function(FunctionName=function_name)["Configuration"]["FunctionArn"]
            print(f"[Lambda] Updated {function_name}: {fn_arn}")
            break
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "InvalidParameterValueException" and attempt < 5:
                print(f"[Lambda] IAM role not ready yet, retrying in 5s... (attempt {attempt + 1}/6)")
                time.sleep(5)
            else:
                raise

    _wait_active(function_name)
    return fn_arn


# ---------------------------------------------------------------------------
# 4. API Gateway — generic route wiring
# ---------------------------------------------------------------------------

def ensure_route(api_id: str, function_name: str, fn_arn: str, route_key: str) -> None:
    method, path = route_key.split(" ", 1)
    print(f"[APIGW] Ensuring route {route_key} on API {api_id}...")

    lambda_integration_uri = (
        f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{fn_arn}/invocations"
    )

    integrations = apigw.get_integrations(ApiId=api_id).get("Items", [])
    existing_integration = next(
        (i for i in integrations if i.get("IntegrationUri") == lambda_integration_uri), None
    )
    if existing_integration is None:
        integration = apigw.create_integration(
            ApiId=api_id,
            IntegrationType="AWS_PROXY",
            IntegrationUri=lambda_integration_uri,
            PayloadFormatVersion="2.0",
        )
        integration_id = integration["IntegrationId"]
        print(f"[APIGW] Created integration: {integration_id}")
    else:
        integration_id = existing_integration["IntegrationId"]
        print(f"[APIGW] Integration already exists: {integration_id}")

    routes = apigw.get_routes(ApiId=api_id).get("Items", [])
    existing_route = next((r for r in routes if r.get("RouteKey") == route_key), None)
    if existing_route is None:
        apigw.create_route(ApiId=api_id, RouteKey=route_key, Target=f"integrations/{integration_id}")
        print(f"[APIGW] Created route: {route_key}")
    else:
        print(f"[APIGW] Route already exists: {route_key}")

    source_arn = f"arn:aws:execute-api:{REGION}:{ACCOUNT_ID}:{api_id}/*/*{path}"
    try:
        aws_lambda.add_permission(
            FunctionName=function_name,
            StatementId="apigw-invoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=source_arn,
        )
        print(f"[Lambda] Added API Gateway invoke permission for {function_name}.")
    except aws_lambda.exceptions.ResourceConflictException:
        print(f"[Lambda] Invoke permission already exists for {function_name}.")


def ensure_api() -> str:
    print(f"[APIGW] Ensuring HTTP API '{API_NAME}' exists...")
    existing_apis = apigw.get_apis().get("Items", [])
    api = next((a for a in existing_apis if a["Name"] == API_NAME), None)

    cors_config = {
        "AllowOrigins": CORS_ALLOW_ORIGINS,
        "AllowMethods": ["GET", "POST", "OPTIONS"],
        "AllowHeaders": ["Content-Type", "Authorization"],
        "AllowCredentials": True,
        "MaxAge": 300,
    }

    if api is None:
        api = apigw.create_api(Name=API_NAME, ProtocolType="HTTP", CorsConfiguration=cors_config)
        print(f"[APIGW] Created API: {api['ApiId']}")
        return api["ApiId"]

    print(f"[APIGW] API already exists: {api['ApiId']}")
    try:
        apigw.update_api(ApiId=api["ApiId"], CorsConfiguration=cors_config)
        print("[APIGW] CORS config updated (credentials + Authorization header allowed).")
    except ClientError as e:
        print(f"[APIGW] Warning updating CORS: {e}")
    return api["ApiId"]


def ensure_stage(api_id: str) -> None:
    print(f"[APIGW] Ensuring stage '{STAGE_NAME}'...")
    stages = apigw.get_stages(ApiId=api_id).get("Items", [])
    existing = next((s for s in stages if s["StageName"] == STAGE_NAME), None)

    if existing is None:
        apigw.create_stage(ApiId=api_id, StageName=STAGE_NAME, AutoDeploy=True)
        print(f"[APIGW] Created and deployed stage '{STAGE_NAME}'.")
    else:
        apigw.update_stage(ApiId=api_id, StageName=STAGE_NAME, AutoDeploy=True)
        print(f"[APIGW] Stage '{STAGE_NAME}' redeployed.")


# ---------------------------------------------------------------------------
# 5. Function definitions — (name, handler file, description, route)
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print(" emeraldbride infrastructure setup")
    print("=" * 60)

    auth_config = load_auth_config()
    jwt_env = {"JWT_SECRET": auth_config["JWT_SECRET"]}

    role_arn = ensure_iam_role()
    api_id = ensure_api()

    # Existing content-management functions: now require a valid session
    # (they import auth_lib.require_auth), so each needs auth_lib.py bundled
    # into its zip and JWT_SECRET available to verify tokens.
    protected_functions = [
        ("emeraldbride-presign-upload", "lambda_function.py", "lambda_function.lambda_handler",
         "Generates presigned S3 PUT URLs for emeraldbride gallery uploads", "GET /presign-upload"),
        ("emeraldbride-save-gallery-state", "save_state.py", "save_state.lambda_handler",
         "Persists gallery state JSON to S3 for emeraldbride site", "POST /save-gallery-state"),
        ("emeraldbride-save-hero-state", "save_hero_state.py", "save_hero_state.lambda_handler",
         "Persists hero state JSON to S3 for emeraldbride site", "POST /save-hero-state"),
        ("emeraldbride-list-gallery", "list_gallery.py", "list_gallery.lambda_handler",
         "Lists S3 gallery photos for the emeraldbride admin panel", "GET /list-gallery"),
    ]

    for function_name, source_file, handler, description, route_key in protected_functions:
        zip_bytes = build_zip(source_file, AUTH_LIB_FILE)
        fn_arn = ensure_lambda(function_name, handler, description, role_arn, zip_bytes, environment=jwt_env)
        ensure_route(api_id, function_name, fn_arn, route_key)

    # Auth functions: oauth-callback needs the full Google config + JWT_SECRET
    # (it issues sessions); verify/logout only need JWT_SECRET.
    oauth_callback_env = {**jwt_env, **{k: v for k, v in auth_config.items() if k != "JWT_SECRET"}}

    auth_functions = [
        ("emeraldbride-oauth-callback", "oauth_callback.py", "oauth_callback.lambda_handler",
         "Exchanges a Google authorization code for a signed emeraldbride session cookie",
         "POST /oauth-callback", oauth_callback_env),
        ("emeraldbride-oauth-verify", "oauth_verify.py", "oauth_verify.lambda_handler",
         "Validates the emeraldbride session cookie", "GET /oauth-verify", jwt_env),
        ("emeraldbride-oauth-logout", "oauth_logout.py", "oauth_logout.lambda_handler",
         "Clears the emeraldbride session cookie", "POST /oauth-logout", jwt_env),
    ]

    for function_name, source_file, handler, description, route_key, env in auth_functions:
        zip_bytes = build_zip(source_file, AUTH_LIB_FILE)
        fn_arn = ensure_lambda(function_name, handler, description, role_arn, zip_bytes, environment=env)
        ensure_route(api_id, function_name, fn_arn, route_key)

    ensure_stage(api_id)

    base_url = f"https://{api_id}.execute-api.{REGION}.amazonaws.com/{STAGE_NAME}"
    print()
    print("=" * 60)
    print(" Done!")
    for _, _, _, _, route_key in protected_functions:
        print(f" {route_key:<28} {base_url}{route_key.split(' ', 1)[1]}")
    for _, _, _, _, route_key, _ in auth_functions:
        print(f" {route_key:<28} {base_url}{route_key.split(' ', 1)[1]}")
    print("=" * 60)
    print(f" Google OAuth redirect URI in use: {auth_config['GOOGLE_REDIRECT_URI']}")
    print(" Make sure this exact URI is registered in Google Cloud Console")
    print(" under the OAuth client's Authorized redirect URIs.")
    print("=" * 60)


if __name__ == "__main__":
    main()
