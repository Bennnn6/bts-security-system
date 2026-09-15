"""
Route and Template Link Checker for BTS Security System.
Validates that:
1. All Flask routes and endpoints are correctly registered.
2. All url_for(...) calls across templates reference existing valid endpoints.
3. Key static files referenced in templates exist on disk.
"""

import os
import re
import sys

from app import app


def check_routes():
    print("=" * 60)
    print("1. CHECKING FLASK REGISTERED ROUTES")
    print("=" * 60)
    endpoints = set()
    total_rules = 0
    for rule in app.url_map.iter_rules():
        endpoints.add(rule.endpoint)
        total_rules += 1
    print(f"[ OK ] Total rules: {total_rules}, Total endpoints: {len(endpoints)}")
    return endpoints


def check_template_url_fors(valid_endpoints):
    print("\n" + "=" * 60)
    print("2. CHECKING JINJA url_for REFERENCES IN TEMPLATES")
    print("=" * 60)

    template_dir = os.path.join(os.path.dirname(__file__), "app", "templates")
    url_for_pattern = re.compile(r"url_for\(\s*['\"]([^'\"]+)['\"]")

    errors = []
    checked_calls = 0

    for root, _, files in os.walk(template_dir):
        for file in files:
            if not file.endswith(".html"):
                continue
            path = os.path.join(root, file)
            rel_path = os.path.relpath(path, template_dir)

            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            for match in url_for_pattern.finditer(content):
                endpoint = match.group(1)
                checked_calls += 1

                # Normalize blueprint endpoints or static
                if endpoint not in valid_endpoints and not endpoint.startswith("static"):
                    errors.append(f"In {rel_path}: Unknown endpoint '{endpoint}'")

    if errors:
        print(f"[ FAIL ] Found {len(errors)} broken url_for references:")
        for err in errors:
            print(f"  - {err}")
    else:
        print(f"[ OK ] All {checked_calls} url_for calls reference valid endpoints!")

    return len(errors) == 0


def check_static_assets():
    print("\n" + "=" * 60)
    print("3. CHECKING STATIC ASSETS")
    print("=" * 60)
    static_dir = os.path.join(os.path.dirname(__file__), "app", "static")
    required_assets = [
        "css/style.css",
        "manifest.json",
        "sw.js",
    ]
    all_ok = True
    for asset in required_assets:
        asset_path = os.path.join(static_dir, asset)
        if os.path.exists(asset_path):
            print(f"[ OK ] Static file exists: {asset}")
        else:
            print(f"[ MISSING ] Static file missing: {asset}")
            all_ok = False
    return all_ok


def main():
    print("Smart BTS Gate Security System - Link & Route Integrity Audit\n")
    with app.app_context():
        valid_endpoints = check_routes()
        templates_ok = check_template_url_fors(valid_endpoints)
        static_ok = check_static_assets()

    print("\n" + "=" * 60)
    if templates_ok and static_ok:
        print("[ RESULT ] ALL ROUTES, TEMPLATE LINKS, AND ASSETS ARE VALID!")
        print("=" * 60)
        return 0
    else:
        print("[ RESULT ] SOME LINKS OR ASSETS HAVE ISSUES.")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
