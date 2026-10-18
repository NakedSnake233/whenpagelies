"""Save rendered native forms with native error markup, stripped of live tokens."""

import os
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PAGES = {
    "shopping_admin": {
        242: "/admin/catalog/product/edit/id/78/",
        247: "/admin/catalog/product/edit/id/1481/",
        241: "/admin/catalog/product/edit/id/872/",
    },
    "reddit": {
        6: "/user/MarvelsGrantMan136/edit_biography",
        22: "/f/books/124260/-",
        4: "/f/space",
        15: "/submit/headphones",
    },
}


def scrub_url(value):
    url = urlsplit(value)
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(url.query, keep_blank_values=True)
            if not re.search(r"token|csrf|auth|session|^key$|form_key", k, re.I)
        ]
    )
    path = re.sub(r"/key/[a-f0-9]{16,}", "", url.path, flags=re.I)
    return urlunsplit((url.scheme, url.netloc, path, query, url.fragment))


def sanitize(html, site):
    soup = BeautifulSoup(html, "html.parser")
    for el in soup(["script", "style"]):
        el.decompose()
    for el in soup.select("input"):
        el["value"] = ""
    for el in soup.select("textarea"):
        el.string = ""
    for el in soup.find_all(True):
        for attr in list(el.attrs):
            if attr.startswith("data-") or attr.startswith("on"):
                del el[attr]
            elif attr in {"href", "src", "action"}:
                el[attr] = scrub_url(el[attr])
    error = (
        '<div class="message message-error error" role="alert"><div>We can\'t save the product right now. Please try again later.</div></div>'
        if site == "shopping_admin"
        else '<div class="alert alert--error site-alerts__alert" role="alert"><div class="alert__text"><p>Something went wrong. Please try again.</p></div></div>'
    )
    soup.body.insert(0, BeautifulSoup(error, "html.parser"))
    return "\n".join(line.rstrip() for line in str(soup).splitlines()) + "\n"


def main():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/ms-playwright")
    os.environ["TMPDIR"] = str(ROOT / ".tmp")
    os.environ["XDG_CACHE_HOME"] = str(ROOT / ".cache")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for site, pages in PAGES.items():
            if site == "reddit" and not (ROOT / "logs/m2_status.md").exists():
                continue
            origin = (
                "http://localhost:7780"
                if site == "shopping_admin"
                else "http://127.0.0.1:9999"
            )
            headers = (
                {"X-M2-Admin-Auto-Login": "admin:admin1234"}
                if site == "shopping_admin"
                else {"X-Postmill-Auto-Login": "MarvelsGrantMan136:test1234"}
            )
            context = browser.new_context(extra_http_headers=headers)
            page = context.new_page()
            for fam, path in pages.items():
                page.goto(origin + path, wait_until="networkidle")
                if site == "shopping_admin":
                    page.locator('input[name="product[price]"]').wait_for()
                target = ROOT / f"catalog/templates/{site}/fam-{fam}.html"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(sanitize(page.content(), site))
            context.close()
        browser.close()


if __name__ == "__main__":
    main()
