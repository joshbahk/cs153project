from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
import warnings
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient` is deprecated")

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from contracts.study_spec import BudgetState, CreditStatus
from db.repository import create_paper_run, get_run
from db.session import create_app_engine, create_session_factory, init_database
from ingest.paper_ingest import PaperIngestError, build_paper_input, extract_pdf_text
from ingest.study_loader import StudyDocument
from pipeline.service import PipelineConfig, execute_documents
from web.app import create_app
from web.settings import AppSettings
from worker.jobs import process_next_run


PAPER_TEXT = """Hypothesis: social norm statements improve compliance intention.
Condition control prompt: Please complete this task if you can.
Condition treatment prompt: Most people like you complete this task quickly.
The article described N = 120 participants and p-value below 0.05.
The outcome is a continuous compliance intention score."""


def _credit() -> CreditStatus:
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    return CreditStatus(
        provider="digitalocean",
        validated=True,
        expires_at=future,
        eligible_products=["app_platform"],
        blocked_products=["gpu_droplets"],
    )


class IngestWebStorageTest(unittest.TestCase):
    def test_text_ingestion_caps_and_hashes(self) -> None:
        paper = build_paper_input(
            title="Norm Study",
            pasted_text=PAPER_TEXT,
            upload_bytes=None,
            upload_filename="",
            max_chars=500,
        )
        self.assertEqual(paper.title, "Norm Study")
        self.assertEqual(paper.source_kind, "text")
        self.assertEqual(len(paper.source_sha256), 64)

    def test_blank_pdf_is_rejected_as_empty_or_scanned(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            writer.write(tmp)
            tmp.seek(0)
            with self.assertRaises(PaperIngestError):
                extract_pdf_text(tmp.read(), max_upload_mb=15, max_chars=120_000)

    def test_pipeline_is_deterministic_and_uses_unique_agents(self) -> None:
        doc = StudyDocument(
            study_id="study_a",
            title="Study A",
            source_uri="test://study-a",
            text=PAPER_TEXT,
            source_sha256="hash",
        )
        config = PipelineConfig(seed=9, max_sample_size=120)
        kwargs = {
            "docs": [doc],
            "run_id": "run_a",
            "budget_state": BudgetState(250.0, 25.0, {"extraction": 50.0, "simulation": 150.0, "analysis": 25.0, "reporting": 25.0}),
            "credits": [_credit()],
            "config": config,
        }
        first = execute_documents(**kwargs)
        second = execute_documents(**kwargs)
        self.assertEqual(first.analysis, second.analysis)
        rows = first.trials["study_a"]
        ids = [row["agent_id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_storage_and_worker_status_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url = f"sqlite:///{tmpdir}/runs.db"
            engine = create_app_engine(db_url)
            init_database(engine)
            sessions = create_session_factory(engine)
            settings = AppSettings(database_url=db_url, max_sample_size=120, require_credit_validation=True)
            paper = build_paper_input("Norm Study", PAPER_TEXT, None, "")

            with sessions() as session:
                run = create_paper_run(session, paper, seed=153)
                self.assertEqual(run.status, "queued")

            self.assertTrue(process_next_run(sessions, settings))

            with sessions() as session:
                out = get_run(session, run.id)
                self.assertIsNotNone(out)
                self.assertEqual(out.status, "succeeded")
                self.assertIsNotNone(out.analysis_json)

    def test_fastapi_routes_and_queue_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = AppSettings(
                database_url=f"sqlite:///{tmpdir}/web.db",
                max_sample_size=120,
                max_queued_jobs=1,
                auto_process_on_submit=False,
            )
            app = create_app(settings)
            client = TestClient(app)

            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn("Replication Triage", home.text)

            created = client.post(
                "/runs",
                data={"title": "Norm Study", "paper_text": PAPER_TEXT},
                follow_redirects=False,
            )
            self.assertEqual(created.status_code, 303)
            run_path = created.headers["location"]

            capped = client.post("/runs", data={"title": "Second", "paper_text": PAPER_TEXT})
            self.assertEqual(capped.status_code, 200)
            self.assertIn("Queue limit reached", capped.text)

            detail = client.get(run_path)
            self.assertEqual(detail.status_code, 200)
            self.assertIn("queued", detail.text)

            api = client.get("/api/runs")
            self.assertEqual(api.status_code, 200)
            self.assertEqual(len(api.json()["items"]), 1)


if __name__ == "__main__":
    unittest.main()
