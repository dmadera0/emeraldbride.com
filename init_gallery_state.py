"""
Reads DEFAULTS.gallery from index.html, writes gallery-state.json to S3.
"""

import json
import re
import sys
import boto3

PROFILE = "emeraldbride"
REGION  = "us-east-1"
BUCKET  = "emeraldbride-site"
KEY     = "gallery-state.json"


def extract_gallery(html: str) -> list[dict]:
    # Slice the text between "gallery: [" and its closing "],"
    start = html.find("gallery: [")
    if start == -1:
        raise ValueError("Could not find 'gallery: [' in index.html")
    start = html.index("[", start)

    # Walk forward counting brackets to find the matching close
    depth = 0
    end   = start
    for i, ch in enumerate(html[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    else:
        raise ValueError("Unmatched '[' — could not find end of gallery array")

    block = html[start + 1 : end]  # contents between the outer [ ]

    # Match each JS object literal: { key: 'value', ... }
    # Keys are unquoted identifiers; values are single-quoted strings.
    obj_re   = re.compile(r"\{([^}]+)\}")
    field_re = re.compile(r"(\w+)\s*:\s*'([^']*)'")

    entries = []
    for obj_match in obj_re.finditer(block):
        fields = dict(field_re.findall(obj_match.group(1)))
        if "url" not in fields:
            continue
        entries.append({
            "url":        fields.get("url", ""),
            "caption":    fields.get("caption", ""),
            "alt":        fields.get("alt", ""),
            "focalPoint": fields.get("focalPoint", "50% 50%"),
            "hidden":     False,
        })

    return entries


def main():
    try:
        with open("index.html", encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        print("Error: index.html not found in the current directory.", file=sys.stderr)
        sys.exit(1)

    gallery = extract_gallery(html)
    if not gallery:
        print("Error: no gallery entries found — check the regex or HTML structure.", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps(gallery, ensure_ascii=False, indent=2)

    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    s3      = session.client("s3")
    s3.put_object(
        Bucket      = BUCKET,
        Key         = KEY,
        Body        = payload.encode("utf-8"),
        ContentType = "application/json",
        CacheControl= "no-cache, no-store",
    )

    print(f"Wrote {len(gallery)} entries to s3://{BUCKET}/{KEY}")
    print("Done.")


if __name__ == "__main__":
    main()
