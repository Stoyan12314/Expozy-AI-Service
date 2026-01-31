import pytest
import api.orchestrator.ai.providers.ai_provider as ai


def _base_pkg():
 
    return {
        "metadata": {"route": "/x", "pageType": "landing"},
        "theme": {"primaryColor": "#3B82F6", "darkMode": False},
        "dataSources": [{"id": "ds1", "endpoint": "get.products", "params": {"limit": 12}}],
        "actions": [{"id": "ac1", "endpoint": "Shop.post_carts", "method": "POST"}],
        "sections": [
            {"type": "hero", "title": "Hi"},
            {"type": "products", "dataSource": "ds1", "buttons": [{"label": "Add", "actionRef": "ac1"}]},
        ],
    }


def test_valid_template_has_no_errors():
    pkg = _base_pkg()
    res = ai.TemplateValidator.validate(pkg)

    assert res.valid is True
    assert res.has_errors() is False
    assert res.all_errors() == []


@pytest.mark.parametrize(
    "endpoint, expected_substr",
    [
        ("/api/products", "invalid format"),
        ("delete.users", "dangerous pattern"),
        ("Drop.Table", "invalid format"),  
    ],
)
def test_endpoint_validation_flags_bad_endpoints(endpoint, expected_substr):
    pkg = _base_pkg()
    pkg["dataSources"][0]["endpoint"] = endpoint

    res = ai.TemplateValidator.validate(pkg)
    assert res.valid is False
    assert any(expected_substr in e for e in res.endpoint_errors)


def test_crossref_unknown_datasource_is_flagged():
    pkg = _base_pkg()
    pkg["sections"][1]["dataSource"] = "missing_ds"

    res = ai.TemplateValidator.validate(pkg)
    assert res.valid is False
    assert any("references unknown id" in e for e in res.crossref_errors)


def test_crossref_unknown_actionref_is_flagged():
    pkg = _base_pkg()
    pkg["sections"][1]["buttons"][0]["actionRef"] = "missing_action"

    res = ai.TemplateValidator.validate(pkg)
    assert res.valid is False
    assert any("references unknown id" in e for e in res.crossref_errors)


def test_security_flags_script_tag():
    pkg = _base_pkg()
    pkg["sections"][0]["title"] = "<script>alert(1)</script>"

    res = ai.TemplateValidator.validate(pkg)
    assert res.valid is False
    assert any("script tag" in s for s in res.security_flags)


def test_alpine_unsafe_patterns_are_flagged():
    pkg = _base_pkg()
    pkg["sections"][0]["html"] = '<div x-html="userInput"></div>'

    res = ai.TemplateValidator.validate(pkg)
    assert res.valid is False
    assert any("x-html" in e.lower() for e in res.alpine_errors)


def test_tailwind_classname_too_long_is_flagged():
    pkg = _base_pkg()
    pkg["sections"][0]["className"] = "x" * (ai.MAX_CLASSNAME_LENGTH + 1)

    res = ai.TemplateValidator.validate(pkg)
    assert res.valid is False
    assert any("exceeds max length" in e for e in res.tailwind_errors)


def test_theme_invalid_color_is_flagged():
    pkg = _base_pkg()
    pkg["theme"]["primaryColor"] = "not-a-color"

    res = ai.TemplateValidator.validate(pkg)
    assert res.valid is False
    assert any("not a valid hex color" in e for e in res.theme_errors)


def test_route_traversal_is_flagged():
    pkg = _base_pkg()
    pkg["metadata"]["route"] = "/../admin"

    res = ai.TemplateValidator.validate(pkg)
    assert res.valid is False
    assert any("directory traversal" in e for e in res.route_errors)


def test_page_type_warnings_contact_missing_fields():
    pkg = {
        "metadata": {"route": "/contact", "pageType": "contact"},
        "sections": [{"type": "form", "fields": [{"name": "email"}]}],  # missing name/message
    }

    res = ai.TemplateValidator.validate(pkg)
    # warnings only (not necessarily errors)
    assert res.has_warnings() is True
    assert any("requires form field" in w for w in res.page_type_warnings)


def test_completeness_warning_form_no_actionref():
    pkg = {
        "metadata": {"route": "/contact", "pageType": "contact"},
        "sections": [{"type": "form", "fields": [{"name": "name"}]}],  # no actionRef
    }

    res = ai.TemplateValidator.validate(pkg)
    assert res.has_warnings() is True
    assert any("form section has no actionRef" in w for w in res.completeness_warnings)
