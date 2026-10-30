"""Read-only smoke coverage for the dashboard prototype data model."""
from pathlib import Path

from moat.dashboard.prototype_data import (
    latest_run_id,
    load_company_detail,
    load_gallery_data,
    most_complete_committee_run_id,
)
from moat.db.connection import get_connection


APP_PATH = str(Path(__file__).resolve().parents[1] / "moat" / "dashboard" / "prototypes.py")


def test_prototype_data_loads_real_repository_database():
    db_path = Path(__file__).resolve().parents[1] / "data" / "moat.db"
    conn = get_connection(db_path=db_path)
    data = load_gallery_data(conn)

    assert data["funnel"]["universe"] > 0
    assert not data["committee"].empty
    assert data["committee"]["overall_score"].is_monotonic_decreasing
    assert data["funnel"]["investigate"] == 2
    assert data["runs"]["committee"] == "20260930T133528Z"

    ticker = data["committee"].iloc[0]["ticker"]
    detail = load_company_detail(conn, ticker, data)
    assert detail["company"]["ticker"] == ticker
    assert detail["verdict"]["status"] in {"Investigate", "Watch", "Reject"}
    conn.close()


def test_latest_run_rejects_unknown_table():
    conn = get_connection()
    try:
        try:
            latest_run_id(conn, "companies")
        except ValueError as error:
            assert "Unsupported" in str(error)
        else:
            raise AssertionError("latest_run_id accepted an unsafe table name")
    finally:
        conn.close()


def test_committee_selection_prefers_complete_batch_over_later_shard():
    conn = get_connection()
    try:
        selected = most_complete_committee_run_id(conn)
        selected_count = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM committee_verdicts WHERE run_id = ?",
            (selected,),
        ).fetchone()[0]
        max_nonfailed_count = conn.execute(
            """
            SELECT MAX(verdict_count) FROM (
                SELECT COUNT(DISTINCT cv.ticker) AS verdict_count
                FROM committee_verdicts cv
                JOIN pipeline_runs pr ON pr.run_id = cv.run_id
                WHERE pr.status != 'failed'
                GROUP BY cv.run_id
            )
            """
        ).fetchone()[0]
        assert selected_count == max_nonfailed_count
    finally:
        conn.close()


def test_all_five_prototype_views_render():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(APP_PATH, default_timeout=60)
    app.run()
    choices = [
        "01 · Decision desk",
        "02 · Company workbench",
        "03 · Opportunity map",
        "04 · Evidence room",
        "05 · Demo storyboard",
    ]
    for choice in choices:
        app.radio[0].set_value(choice).run()
        assert not app.exception, choice
