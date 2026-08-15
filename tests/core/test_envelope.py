from app.core.envelope import Meta, error_response, success_response


def test_success_shape():
    assert success_response({"a": 1}) == {"data": {"a": 1}, "meta": None}


def test_success_with_meta():
    out = success_response([1, 2], Meta(next_cursor="abc", total_estimate=9))
    assert out["meta"]["next_cursor"] == "abc"
    assert out["meta"]["total_estimate"] == 9


def test_error_shape():
    out = error_response("EMAIL_TAKEN", "taken", [{"field": "email", "message": "taken"}])
    assert out == {
        "error": {
            "code": "EMAIL_TAKEN",
            "message": "taken",
            "field_errors": [{"field": "email", "message": "taken"}],
        }
    }


def test_error_defaults_empty_field_errors():
    assert error_response("X", "y")["error"]["field_errors"] == []
