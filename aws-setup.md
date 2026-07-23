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

## 7. admin.html Access Control

### Current state (Google OAuth)
`admin.html` is gated by Google OAuth 2.0, not a shared password. Signing in requires a real Google account that's on an allowlist (specific emails and/or a Google Workspace domain) checked server-side by `emeraldbride-oauth-callback` — Google authenticating someone only proves who they are, not that they're allowed to administer this site, so the allowlist check is what actually authorizes them. A successful login gets a signed (HMAC-SHA256), httpOnly, `SameSite=None; Secure` session cookie with an 8-hour TTL; the token is never readable by page JavaScript.

Every content-mutating API endpoint (`presign-upload`, `save-gallery-state`, `save-hero-state`, `list-gallery`) independently verifies that session cookie via `auth_lib.require_auth()` before doing anything — gating only the HTML page and leaving the API open would mean anyone who found the API Gateway URL (which ships in the page's own JS) could bypass the login screen entirely.

Relevant files: `auth_lib.py` (shared JWT/allowlist helpers, bundled into every Lambda zip), `oauth_callback.py`, `oauth_verify.py`, `oauth_logout.py`, `auth.js` (frontend), and the auth checks added to the four existing content Lambdas. Deployment (including required env vars) is documented in the docstring at the top of `deploy_upload_lambda.py`.

### Optional extra layer

A CloudFront Function doing HTTP Basic Auth in front of `/admin.html` (edge-layer, before the request even reaches S3/OAuth) is still a reasonable belt-and-suspenders addition if you want defense in depth beyond OAuth — see CloudFront → Functions in the AWS Console if you want to add one. Not required now that real authentication is in place.

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

## 8b. Google Search Console Setup

After the site is live at emeraldbride.com, submit it to Google Search Console
to accelerate indexing. This is free and takes 10 minutes.

**Steps:**
1. Go to search.google.com/search-console
2. Add property → URL prefix → `https://emeraldbride.com`
3. Verify ownership via HTML tag method:
   - Google provides a `<meta name="google-site-verification" content="...">` tag
   - Add it to index.html `<head>` immediately after the canonical tag
   - Deploy to S3 and invalidate CloudFront
   - Click Verify in Search Console
4. Once verified, go to Sitemaps → Add sitemap → enter `sitemap.xml`
5. Submit and wait 24–48 hours for Google to crawl

**After submission, monitor:**
- Coverage report: confirms pages are indexed
- Core Web Vitals: flags CLS, LCP, or FID issues
- Rich Results: confirms FAQ schema and LocalBusiness schema are valid
  (also test at search.google.com/test/rich-results before submitting)
- Image indexing: check Google Images search for `site:emeraldbride.com`

**Add this placeholder to index.html `<head>` now, ready for the verification code:**
```html
<!-- Google Search Console verification — replace content value after verifying -->
<!-- <meta name="google-site-verification" content="REPLACE_WITH_GSC_CODE" /> -->
```

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

**4. Real admin authentication — done**
- Implemented as Google OAuth 2.0 rather than Cognito or a custom login form — see section 7 above for the current design (`emeraldbride-oauth-callback`/`-verify`/`-logout`, `auth_lib.py`, `auth.js`)
- Session is a signed JWT in an httpOnly cookie rather than an `Authorization: Bearer` header the client holds, so a page XSS can't exfiltrate it
- Every protected endpoint calls `auth_lib.require_auth(event)` to validate the session before doing anything

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
