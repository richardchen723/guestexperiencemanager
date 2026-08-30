from pathlib import Path


def test_ticket_filter_panel_stays_above_ticket_content():
    project_root = Path(__file__).resolve().parents[3]
    stylesheet = (project_root / "dashboard/static/css/style.css").read_text()
    base_template = (project_root / "dashboard/templates/base.html").read_text()

    filter_rule = stylesheet.split(".tickets-filters-modern {", 1)[1].split("}", 1)[0]
    assert "position: relative;" in filter_rule
    assert "z-index: 30;" in filter_rule
    assert "filename='css/style.css', v='20260830-2'" in base_template
