import unittest
from pathlib import Path

from dashboard.api.documentation import read_api_markdown, render_api_markdown


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class ApiDocumentationTests(unittest.TestCase):
    def test_canonical_document_contains_agent_endpoint(self):
        markdown = read_api_markdown()
        self.assertIn("GET /api/v1/guest-issues", markdown)
        self.assertIn("guest_issues:read", markdown)

    def test_renderer_builds_navigation_and_escapes_raw_html(self):
        rendered, toc = render_api_markdown(
            "# API\n\n## Read data\n\nUse `GET /api/data`.\n\n<script>alert(1)</script>"
        )

        self.assertIn('<h2 id="read-data">', rendered)
        self.assertIn("<code>GET /api/data</code>", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertEqual(toc, [{"level": 2, "title": "Read data", "anchor": "read-data"}])

    def test_duplicate_headings_receive_stable_unique_anchors(self):
        rendered, toc = render_api_markdown("## API\n\n## API")

        self.assertIn('id="api"', rendered)
        self.assertIn('id="api-2"', rendered)
        self.assertEqual([item["anchor"] for item in toc], ["api", "api-2"])

    def test_navigation_links_to_documentation_on_desktop_and_mobile(self):
        base_template = (PROJECT_ROOT / "dashboard/templates/base.html").read_text()
        mobile_navigation = (PROJECT_ROOT / "dashboard/static/js/mobile-nav.js").read_text()

        self.assertIn("url_for('api_docs')", base_template)
        self.assertIn("<span>API documentation</span>", base_template)
        self.assertIn("label: 'API documentation'", mobile_navigation)
        self.assertIn("url: '/api-docs'", mobile_navigation)

    def test_documentation_page_uses_markdown_links_without_clipboard_logic(self):
        template = (PROJECT_ROOT / "dashboard/templates/api-docs.html").read_text()

        self.assertIn('href="/api-docs.md"', template)
        self.assertNotIn("navigator.clipboard", template)
        self.assertNotIn("copyDocs", template)
        self.assertNotIn("Copy for AI agent", template)
        self.assertNotIn("Copy document", template)


if __name__ == "__main__":
    unittest.main()
