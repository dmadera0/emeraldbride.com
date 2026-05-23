# AWS Deployment Guide — Emerald Bride (emeraldbride.com)

## Architecture Overview

```
User → Route 53 (DNS) → CloudFront (CDN + HTTPS) → S3 (static files)
                         ACM (SSL cert, us-east-1)
```

S3 hosts the files. CloudFront serves them globally over HTTPS. Route 53 handles DNS. ACM provides the SSL certificate. This stack costs ~$1–2/month, delivers sub-100ms load times from anywhere in the world, and is structured so adding Lambda + API Gateway later requires zero changes to the frontend.

---

## 1. Prerequisites

### AWS Account Setup
- Use an IAM user — **never the root account** — for all CLI operations
- Create an IAM user with these managed policies attached:
  - `AmazonS3FullAccess`
  - `CloudFrontFullAccess`
  - `AmazonRoute53FullAccess`
  - `AWSCertificateManagerFullAccess`
- Generate an access key for this IAM user (IAM → User → Security credentials → Create access key)

### AWS CLI
```bash
# Install (macOS)
brew install awscli

# Configure
aws configure
# AWS Access Key ID:     [your key]
# AWS Secret Access Key: [your secret]
# Default region name:   us-east-1
# Default output format: json
```

### Domain
- `emeraldbride.com` must be registered — either via Route 53 (simplest) or transferred in from another registrar
- If registering via Route 53: Route 53 → Registered domains → Register domain

---

## 2. S3 Bucket Setup

### Create the bucket
```bash
aws s3api create-bucket \
  --bucket emeraldbride.com \
  --region us-east-1
```

> **Region must be `us-east-1`** — CloudFront and ACM work together without friction only in this region.

### Block all public access (ON)
```bash
aws s3api put-public-access-block \
  --bucket emeraldbride.com \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

CloudFront will be the **only** access point. Do not make the bucket public.

### Enable static website hosting
```bash
aws s3api put-bucket-website \
  --bucket emeraldbride.com \
  --website-configuration '{
    "IndexDocument": {"Suffix": "index.html"},
    "ErrorDocument": {"Key": "index.html"}
  }'
```

> Error document set to `index.html` so direct URL access to any path works gracefully.

### Upload your files
```bash
aws s3 sync . s3://emeraldbride.com \
  --exclude "*" \
  --include "*.html"
```

### Bucket policy (restrict to CloudFront OAC only)

After creating your CloudFront distribution (step 3), you'll have an OAC ARN. Replace `YOUR_OAC_ARN` and `YOUR_ACCOUNT_ID` below:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCloudFrontServicePrincipal",
      "Effect": "Allow",
      "Principal": {
        "Service": "cloudfront.amazonaws.com"
      },
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::emeraldbride.com/*",
      "Condition": {
        "StringEquals": {
          "AWS:SourceArn": "arn:aws:cloudfront::YOUR_ACCOUNT_ID:distribution/YOUR_DISTRIBUTION_ID"
        }
      }
    }
  ]
}
```

Save as `bucket-policy.json`, then apply:
```bash
aws s3api put-bucket-policy \
  --bucket emeraldbride.com \
  --policy file://bucket-policy.json
```

---

## 3. CloudFront Distribution

### Create an Origin Access Control (OAC)

In the AWS Console: CloudFront → Origin access → Create control setting
- Name: `emeraldbride-oac`
- Origin type: S3
- Signing behavior: Sign requests (recommended)
- Signing protocol: SigV4

Note the OAC ID — you'll need it when creating the distribution.

### Create the distribution (Console steps)

**CloudFront → Create distribution:**

| Setting | Value |
|---|---|
| Origin domain | `emeraldbride.com.s3.us-east-1.amazonaws.com` (select from dropdown) |
| Origin access | Origin access control settings → select your OAC |
| Viewer protocol policy | **Redirect HTTP to HTTPS** |
| Allowed HTTP methods | GET, HEAD |
| Cache policy | `CachingOptimized` (AWS managed) |
| Compress objects | Yes |
| Price class | **Use all edge locations** (best global performance) |
| Alternate domain names | `emeraldbride.com` and `www.emeraldbride.com` |
| Custom SSL certificate | Select your ACM cert (see step 4 — must exist first) |
| Default root object | `index.html` |
| Web Application Firewall | Skip for MVP |

**After creation, add a Custom Error Response:**
- CloudFront → Your distribution → Error pages → Create custom error response
- HTTP error code: 403
- Customize error response: Yes
- Response page path: `/index.html`
- HTTP response code: 200

This handles S3 returning 403 on direct path access (e.g., `emeraldbride.com/about`).

**Note your distribution domain:** `xxxx.cloudfront.net` — you'll need it for DNS.

---

## 4. ACM SSL Certificate

> **Critical: certificates for CloudFront must be in `us-east-1` regardless of where your bucket is.**

### Request the certificate

```bash
aws acm request-certificate \
  --domain-name emeraldbride.com \
  --subject-alternative-names "*.emeraldbride.com" \
  --validation-method DNS \
  --region us-east-1
```

This returns a `CertificateArn`. Note it.

### Get the DNS validation records
```bash
aws acm describe-certificate \
  --certificate-arn YOUR_CERT_ARN \
  --region us-east-1 \
  --query "Certificate.DomainValidationOptions"
```

This returns CNAME name/value pairs. Add them to Route 53 (next step).

### Wait for issuance

After DNS records are added, run:
```bash
aws acm wait certificate-validated \
  --certificate-arn YOUR_CERT_ARN \
  --region us-east-1
```

Status will change to `Issued` within 2–5 minutes of DNS propagation. The certificate is **free** and auto-renews.

---

## 5. Route 53 DNS

### Create the hosted zone
```bash
aws route53 create-hosted-zone \
  --name emeraldbride.com \
  --caller-reference $(date +%s)
```

Note the 4 **NS (nameserver) records** returned. If your domain is registered outside of Route 53, log into your registrar and update the nameservers to these 4 values.

### Add DNS records

In Route 53 → Hosted zones → emeraldbride.com → Create record:

**Apex domain (A record — Alias):**
- Record name: (leave blank — apex)
- Record type: A
- Alias: Yes
- Route traffic to: Alias to CloudFront distribution
- Select your distribution (`xxxx.cloudfront.net`)

**www subdomain (A record — Alias):**
- Record name: `www`
- Record type: A
- Alias: Yes
- Route traffic to: Alias to CloudFront distribution
- Select your distribution

**ACM validation CNAMEs** (from step 4):
- Add each CNAME name/value pair provided by ACM
- TTL: 300

**TTL guidance:** Set all new records to TTL 300 during setup. After everything is verified and live, increase to 3600 for better caching.

---

## 6. Deploying Updates

Every time you update site files, run:

```bash
# 1. Upload all static assets (images, xml, txt) with long cache — 1 year
aws s3 sync . s3://emeraldbride.com \
  --exclude "*" \
  --include "*.xml" \
  --include "*.txt" \
  --include "images/*" \
  --cache-control "max-age=31536000" \
  --exclude "images/*.html"

# 2. Upload HTML files with no-cache so visitors always get the latest version
aws s3 sync . s3://emeraldbride.com \
  --exclude "*" \
  --include "*.html" \
  --cache-control "no-cache, no-store, must-revalidate" \
  --content-type "text/html"

# 3. Invalidate CloudFront cache so edge nodes serve the new files immediately
aws cloudfront create-invalidation \
  --distribution-id YOUR_DIST_ID \
  --paths "/*"
```

**Cache strategy:**
- Images and static assets use `max-age=31536000` (1 year) — filenames don't change and CloudFront invalidation handles cache busting when you do update them.
- HTML files use `no-cache` so visitors always get the latest page without needing an explicit invalidation.

**Why both steps?**
- `s3 sync` uploads only changed files (fast, idempotent)
- The CloudFront invalidation clears the CDN cache at all 400+ edge locations so visitors get the new version immediately rather than waiting for cache TTL to expire

**Cost note:** CloudFront provides **1,000 free invalidation paths/month**. Running `/*` counts as 1 path. You can deploy hundreds of times per month for free.

---

## 7. admin.html Access Control (MVP)

### Current state
`admin.html` is protected only by the client-side password check in JavaScript. The file itself is publicly accessible at `emeraldbride.com/admin.html` if someone knows the URL. The hardcoded password (`emerald2025`) is the only gate.

**For MVP, this is acceptable.** The password prevents casual access, and the data it controls (localStorage) lives in the browser anyway.

### Recommended upgrade before going live with real client data

Add a **CloudFront Function** (runs at the edge, free tier: 2M requests/month) that checks HTTP Basic Auth on `/admin.html` before the request ever reaches S3.

---

### Future upgrade — CloudFront Function for admin protection

> **This is a future upgrade, not required for MVP.**
> 
> Create this as a CloudFront Function in the AWS Console (CloudFront → Functions → Create function), then associate it with your distribution's viewer request event for the path `/admin.html`.

```javascript
// CloudFront Function — Basic Auth gate for /admin.html
// Replace ENCODED_CREDENTIALS with: btoa("admin:YOUR_STRONG_PASSWORD")
// Generate it in your browser console: btoa("admin:securepassword123")

function handler(event) {
  var request = event.request;
  var headers = request.headers;

  // Only protect admin.html
  if (request.uri !== '/admin.html') {
    return request;
  }

  var ENCODED_CREDENTIALS = 'YWRtaW46ZW1lcmFsZDIwMjU='; // admin:emerald2025 (change this)

  var authHeader = headers.authorization;

  if (!authHeader || authHeader.value !== 'Basic ' + ENCODED_CREDENTIALS) {
    return {
      statusCode: 401,
      statusDescription: 'Unauthorized',
      headers: {
        'www-authenticate': { value: 'Basic realm="Emerald Bride Admin"' }
      }
    };
  }

  return request;
}
```

**To associate with your distribution:**
1. CloudFront → Your distribution → Behaviors
2. Create a new behavior: Path pattern `/admin.html`
3. Viewer request → Function associations → CloudFront Functions → select your function

This adds a second layer of protection (HTTP Basic Auth in the browser prompt) before any request reaches S3.

---

## 8. Cost Estimate (Monthly)

| Service | Usage | Cost |
|---|---|---|
| S3 storage | < 1MB of HTML | ~$0.00 |
| S3 requests | CloudFront origin fetches | ~$0.01 |
| CloudFront data transfer | First 1TB/month free tier | $0.00–$1.00 |
| CloudFront HTTPS requests | First 10M/month free tier | $0.00 |
| Route 53 hosted zone | 1 zone | $0.50/month |
| Route 53 DNS queries | First 1B/month | ~$0.00–$0.40 |
| ACM certificate | Always free | $0.00 |
| **Total estimated MVP cost** | | **~$1–2/month** |

**Free tier notes:**
- New AWS accounts get 12 months of generous free tier on S3 and CloudFront
- ACM certificates are always free for use with AWS services
- CloudFront's 1TB/month free data transfer applies permanently (not just first year)

---

## 9. Future Backend Upgrade Path

When you need server-side functionality (form submissions, real admin auth, image uploads), the architecture expands cleanly without touching the existing static layer:

### What you'd add

```
                    ┌── S3 (static files — unchanged)
CloudFront ─────────┤
                    └── API Gateway (new: path /api/*)
                              │
                         Lambda Functions
                              │
                    ┌─────────┴──────────┐
                   RDS (PostgreSQL)    S3 (images)
                   or DynamoDB        via presigned URLs
```

### Step-by-step expansion

**1. API Gateway + Lambda**
- Deploy Lambda functions for: form submissions, admin auth, content CRUD
- Create API Gateway with routes like `GET /api/content/:section`, `POST /api/admin/login`
- Add API Gateway as a second origin in your existing CloudFront distribution
- Configure path-based routing: requests to `/api/*` → API Gateway, everything else → S3
- **Zero changes to CloudFront or Route 53** — just add a new origin and behavior

**2. Database**
- Add RDS (PostgreSQL) or DynamoDB to replace localStorage as the content store
- Lambda functions read/write to the database and return JSON to the frontend
- The frontend's data layer is already architected for this — swap `localStorage` calls for `fetch('/api/...')` calls

**3. Image uploads from admin panel**
- Lambda generates S3 presigned URLs (`POST /api/gallery/presign`)
- Admin panel uploads directly to S3 using the presigned URL (no Lambda in the upload path)
- Lambda saves the returned CloudFront URL to the database
- The admin panel already has a comment marking exactly where this flow replaces the URL input field

**4. Real admin authentication**
- Replace hardcoded password with Cognito User Pools or a custom JWT flow
- `POST /api/admin/login` validates credentials, returns a JWT
- Admin panel stores JWT and sends it as `Authorization: Bearer <token>` on subsequent requests
- Lambda middleware validates the JWT on every protected endpoint

**5. Contact form submissions**
- `POST /api/contact` receives form data, stores in database, sends confirmation email via SES
- The form's submit handler already has a comment marking the `// TODO: Replace with POST /api/contact` call

### What stays the same
- CloudFront distribution domain and configuration
- Route 53 DNS records
- ACM SSL certificate
- All frontend HTML/CSS/JS (only the data layer fetch calls change)
- S3 bucket for static files

This is the recommended AWS-native path. The static site layer is completely untouched by the backend expansion — you're only adding origins and Lambda functions alongside it.
