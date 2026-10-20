"""Public OAuth information pages; exact wording approved on 2026-10-20."""
from functools import partial

from flask import make_response, render_template


PUBLIC_PAGES = {
    "about": {"title": "About Cotton Candy", "text": "Cotton Candy, also called Hostaway Insights / Property Management Insights, is a private property-management workspace for its owner and approved staff. It supports property operations, guest-inquiry tracking and bookkeeping. Google sign-in identifies staff; Drive supports bookkeeping evidence; read-only Gmail collects configured inquiries and replies. The collector does not send email or change prices. Staff approval and feature permissions remain required. Contact: yunhang.chen@gmail.com."},
    "privacy": {"title": "Privacy", "text": "Google sign-in supplies account identifiers and profile information. The optional Drive connection requests full Drive access; its bookkeeping workflow uses selected folders and creates or updates evidence files. Gmail access is read-only and can read the designated mailbox, while collection is limited to configured inquiry messages and their threads. Complete originals, message IDs, receipt times, extracted fields and activity history are retained. The collector does not modify Gmail or send its contents to an AI model. Google data passes between Google and Cotton Candy’s production services, with staff access controlled by application permissions. OAuth credentials are stored on the server for ongoing operation. Imported records currently have no automatic expiry; revoking Google access does not erase them. Contact yunhang.chen@gmail.com for corrections or deletion requests, and revoke access through your Google Account when needed."},
    "terms": {"title": "Terms", "text": "Cotton Candy is for its owner and approved staff, within assigned roles and authorized business responsibilities. Protect credentials, provide accurate records and respect access permissions. Staff must review imported information before acting. An inquiry is not a confirmed reservation, price or payment authorization. Google’s terms still apply; application access remains subject to administrator approval. Contact yunhang.chen@gmail.com for support."},
}


def public_page(slug):
    response = make_response(render_template("public/information.html", page=PUBLIC_PAGES[slug], current_page=slug))
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def register_public_pages(app):
    for slug in PUBLIC_PAGES:
        app.add_url_rule("/" + slug, endpoint="public_" + slug, view_func=partial(public_page, slug), methods=["GET"])
