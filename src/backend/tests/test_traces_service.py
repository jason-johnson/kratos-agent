from types import SimpleNamespace

from app.services.traces_service import _build_span_kql, _parse_spans


def test_billing_dimensions_are_queried_and_exposed_as_span_attributes() -> None:
    kql = _build_span_kql("operation_Id == 'operation-1'", lookback_hours=1, max_ops=1)

    assert "kratos.tool.billing.unit" in kql
    assert "kratos.tool.billing.quantity" in kql

    table = SimpleNamespace(
        columns=[
            "operation_Id",
            "timestamp",
            "id",
            "parentId",
            "name",
            "duration",
            "success",
            "kratos_use_case",
            "kratos_skill_name",
            "billing_unit",
            "billing_quantity",
        ],
        rows=[
            [
                "operation-1",
                "2026-09-25T00:00:00Z",
                "span-1",
                "",
                "skill.toolbox_web_search",
                125.0,
                True,
                "generic",
                "kratos-tools-web_search",
                "transaction",
                1,
            ]
        ],
    )

    operations = _parse_spans([table], {}, max_ops=1)

    assert operations[0].spans[0].attributes["kratos.tool.billing.unit"] == "transaction"
    assert operations[0].spans[0].attributes["kratos.tool.billing.quantity"] == 1
