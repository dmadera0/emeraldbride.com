# Emerald Bride — emeraldbride.com

Production-ready website for a Las Vegas bridal makeup artist. Pure HTML/CSS/JS static site — no build tools, no frameworks, no dependencies. Runs locally by opening `index.html` in a browser and deploys to AWS S3 + CloudFront.

---

## Project Structure

```
emeraldbride.com/
├── index.html       # Public-facing website (single page)
├── admin.html       # Password-protected content management panel
├── aws-setup.md     # Step-by-step AWS deployment guide
├── sitemap.xml      # XML sitemap with image entries for Google indexing
├── robots.txt       # Crawler rules — disallows /admin.html
└── images/          # All photos and static assets
```

---

## Website Sections (index.html)

The entire site is one HTML file. Content is stored in `localStorage` and rendered by JavaScript on page load. If no content has been saved via the admin panel, the site renders from `DEFAULTS` — hardcoded fallback values defined near the top of the script block.

### Nav
Fixed top navigation bar. Transparent on load, transitions to a solid dark-emerald background on scroll. Contains links to all sections: Home, About, Services, Gallery, FAQ, Contact. Collapses to a hamburger menu on mobile.

### Hero
Full-viewport section with a background photo, headline, subheadline, and two CTA buttons ("Book a Consultation" → contact section, "View Gallery" → gallery section). The background image is set via JavaScript from the `hero.imageUrl` value in localStorage or the default `./images/hero.jpg`.

### About
Two-column layout: a portrait photo on the left and text on the right. Includes three stat counters (e.g., "200+ Brides", "10 Years Experience"). All copy and the photo URL are editable via the admin panel.

### Services
Three service cards: Bridal Glam, Bridal Party, and Trial Session. Each has a title and description editable in the admin panel. Each card links to the contact section.

### Gallery
Flex-column masonry grid (3 columns desktop / 2 tablet / 1 mobile). Images are rendered as `<img>` tags (not CSS `background-image`) so Google can crawl and index them. Hover shows a caption overlay. Images and captions are manageable via the admin panel.

### FAQ
Accordion list. First item is open by default. All questions and answers are editable in the admin panel. An FAQ schema (`FAQPage` JSON-LD) is generated dynamically and injected into the `<head>` on page load for Google rich results.

### Testimonials
Three client quote cards. Each shows a quote, client name, and wedding date. Editable in the admin panel.

### Contact
Dark-background section. Displays email, Instagram handle, and location. Includes an inquiry form that submits to Formspree. The Formspree endpoint ID is a placeholder — see **Contact Form Setup** below.

### Footer
Logo, tagline, navigation links, social icons (Instagram, Pinterest), copyright line with a dynamic year.

---

## Admin Panel (admin.html)

Password-protected content management interface. Opens at `/admin.html`.

The admin panel saves all content to `localStorage` in the browser. The public `index.html` reads from the same `localStorage` on every page load. This means content changes made in admin are immediately visible on the site in the same browser.

**Important:** localStorage is per-browser and per-device. Content saved in Chrome on one machine does not sync to other devices or browsers. This is intentional for the MVP — the backend upgrade path below replaces localStorage with a real database.

### Admin sections
- **Dashboard** — shows last-saved timestamp and storage usage
- **Hero** — headline, subheadline, background photo URL
- **About** — all copy paragraphs, three stat fields, portrait photo URL
- **Services** — title and description for all three service cards
- **Gallery** — up to 9 images: photo URL, caption, alt text. Drag-and-drop reorder supported.
- **FAQs** — question/answer pairs, add/remove/reorder
- **Testimonials** — quote, name, and date for each card
- **Contact** — email, Instagram, location, response note

---

## Local Development

No build step required.

1. Clone or download the repository
2. Open `index.html` in any modern browser — the full site renders immediately
3. Open `admin.html` to manage content — changes persist in browser localStorage
4. Images in `./images/` are referenced by relative path and work directly from the filesystem

---

## Contact Form Setup (Formspree)

The contact form is wired to Formspree but the endpoint is a placeholder.

1. Go to [formspree.io](https://formspree.io) and create a free account
2. Create a new form — copy the endpoint ID (looks like `xpzgkwqr`)
3. In `index.html`, find:
   ```
   https://formspree.io/f/FORMSPREE_ENDPOINT_ID
   ```
   Replace `FORMSPREE_ENDPOINT_ID` with your actual ID
4. Deploy the updated file to S3

The free Formspree tier allows 50 submissions/month. Upgrade or migrate to a Lambda + SES backend when submission volume grows (see **Future Backend** below).

---

## AWS Deployment

Full step-by-step instructions are in [aws-setup.md](aws-setup.md). Summary:

### Architecture
```
Browser → Route 53 (DNS) → CloudFront (CDN + HTTPS) → S3 (static files)
                            ACM (SSL certificate, us-east-1)
```

### Steps overview
1. **S3** — Create bucket `emeraldbride.com` in `us-east-1`, block all public access, enable static website hosting, upload files
2. **CloudFront** — Create distribution with Origin Access Control (OAC), point to S3 bucket, enable HTTPS redirect, set default root object to `index.html`, add custom error response for 403 → `index.html`
3. **ACM** — Request SSL certificate for `emeraldbride.com` and `*.emeraldbride.com` in `us-east-1`, validate via DNS CNAME records in Route 53
4. **Route 53** — Create hosted zone, add A alias records for apex and `www` pointing to the CloudFront distribution
5. **Deploy updates** — `aws s3 sync` + `aws cloudfront create-invalidation` (see aws-setup.md Step 6 for the exact commands)

### Estimated cost
~$1–2/month (Route 53 hosted zone $0.50 + minimal CloudFront/S3 usage). ACM certificates are always free.

### Google Search Console
After going live, verify ownership and submit `sitemap.xml` to accelerate indexing. Instructions in aws-setup.md Step 8b. A verification placeholder is already in `index.html` `<head>` — uncomment and fill in the code Google provides.

---

## SEO

The following are already implemented:

- Keyword-rich meta description targeting "Las Vegas bridal makeup artist"
- Open Graph and Twitter Card tags with image dimensions
- `geo.region`, `geo.placename`, `geo.position`, and `ICBM` meta tags
- `hreflang` self-referencing tags (`en` and `x-default`)
- `robots` meta tag with `max-image-preview:large` for Google Images
- Schema.org JSON-LD: `LocalBusiness`, `FAQPage` (dynamic), `BreadcrumbList`, `Service` (×3)
- Image sitemap (`sitemap.xml`) with `image:image` entries for all gallery photos
- `<img>` tags with `alt`, `loading="lazy"`, and `decoding="async"` on all gallery images
- `robots.txt` disallowing `/admin.html`

**What still needs to be done before launch:**
- Replace the `og-image.jpg` reference with an actual 1200×630 social share image
- Fill in the Google Search Console verification code once the site is live
- Replace the 3 placeholder reviews in the JSON-LD with real client reviews
- Update `aggregateRating.reviewCount` as real reviews are collected

---

## Future Backend

When the site needs server-side functionality — real admin auth, form submissions to a database, image uploads, or multi-device content sync — the static layer stays completely unchanged. The backend is added alongside it.

### Architecture (expanded)
```
                    ┌── S3 (static files — unchanged)
CloudFront ─────────┤
                    └── API Gateway (new: path /api/*)
                                │
                           Lambda Functions
                                │
                    ┌───────────┴────────────┐
                  RDS (PostgreSQL)        S3 (images)
                  or DynamoDB            via presigned URLs
```

### Upgrade steps

**1. Real admin authentication**
Replace the hardcoded password in `admin.html` with a Cognito User Pool or a custom JWT flow:
- `POST /api/admin/login` validates credentials, returns a signed JWT
- Admin panel stores JWT in memory and sends it as `Authorization: Bearer <token>` on every request
- Lambda middleware validates the JWT on protected endpoints

**2. Content database**
Replace `localStorage` with API calls backed by DynamoDB or RDS:
- Every `getData()` call in `index.html` becomes a `fetch('/api/content/:section')` GET request
- Every save in `admin.html` becomes a `fetch('/api/content/:section', { method: 'PUT', ... })` call
- The data shape is already designed for this — the `DEFAULTS` objects map directly to database records

**3. Contact form**
Replace Formspree with a Lambda function:
- `POST /api/contact` receives form data, stores submission in the database, sends a confirmation email via SES
- The form's submit handler already has a comment marking exactly where the endpoint URL changes

**4. Image uploads**
Replace URL input fields in the admin panel with a real upload flow:
- Admin panel requests a presigned S3 URL via `POST /api/gallery/presign`
- Browser uploads the file directly to S3 using that URL (no Lambda in the upload path — fast, cheap)
- Lambda saves the resulting CloudFront URL to the database
- The admin panel already has comments marking where this flow replaces the URL input

**5. CloudFront routing**
Add API Gateway as a second origin on the existing CloudFront distribution:
- Requests to `/api/*` → API Gateway
- Everything else → S3 (unchanged)
- No changes to Route 53 or ACM — the domain and SSL certificate are unaffected

### What never changes
- CloudFront distribution domain and SSL certificate
- Route 53 DNS records
- S3 bucket for static files
- All HTML/CSS structure in `index.html` — only the data-fetch layer changes
