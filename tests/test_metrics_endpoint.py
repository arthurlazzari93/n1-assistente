import unittest

try:
    from fastapi.testclient import TestClient  # type: ignore
except Exception:  # pragma: no cover
    TestClient = None  # type: ignore

try:
    from app.main import app  # type: ignore
except Exception:  # pragma: no cover
    app = None  # type: ignore


@unittest.skipIf(TestClient is None or app is None, "Dependências FastAPI não disponíveis no ambiente")
class MetricsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)  # type: ignore

    def test_debug_metrics_endpoint_returns_sections(self):
        resp = self.client.get("/debug/metrics")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("ingest", data)
        self.assertIn("followups", data)
        self.assertIn("tickets", data)
        self.assertIn("feedback", data)
