from novel_production_mcp.evals import run_deterministic_evals


def test_bundled_evals() -> None:
    result = run_deterministic_evals()
    assert result["passed"] is True, result
    assert result["total"] >= 8
