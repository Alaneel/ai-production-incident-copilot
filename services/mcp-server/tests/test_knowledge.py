import unittest

from incident_mcp.knowledge import KnowledgeIndex


class KnowledgeIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = KnowledgeIndex.load_default()

    def test_expected_document_is_recalled_for_each_fault_family(self) -> None:
        cases = [
            (
                "payment-service",
                "database connection pool acquisition timeout",
                "runbook://payments/db-pool-saturation",
            ),
            (
                "checkout-api",
                "downstream dependency timeout",
                "runbook://platform/downstream-timeouts",
            ),
            (
                "risk-engine",
                "cpu throttling saturation",
                "runbook://platform/cpu-saturation",
            ),
            (
                "catalog-service",
                "monotonic memory oom leak",
                "runbook://platform/memory-growth",
            ),
            (
                "checkout-api",
                "new revision deployment regression",
                "runbook://delivery/deployment-regression",
            ),
            (
                "ledger-consumer",
                "kafka consumer lag hot partition",
                "runbook://streaming/consumer-lag",
            ),
            (
                "inventory-service",
                "retry amplification dependency errors",
                "runbook://resilience/retry-storm",
            ),
            (
                "catalog-service",
                "cache hit collapse repeated missing keys",
                "runbook://caching/cache-penetration",
            ),
        ]
        for service, query, expected in cases:
            with self.subTest(query=query):
                results = self.index.search(
                    service=service,
                    query=query,
                    content_types={
                        "runbook",
                        "past_incident",
                        "technical_doc",
                    },
                    limit=5,
                )
                self.assertIn(expected, [item["uri"] for item in results])

    def test_results_have_stable_audit_metadata(self) -> None:
        first = self.index.search(
            service="payment-service",
            query="pool saturation v142",
            content_types={"runbook", "past_incident", "technical_doc"},
            limit=5,
        )
        second = self.index.search(
            service="payment-service",
            query="pool saturation v142",
            content_types={"runbook", "past_incident", "technical_doc"},
            limit=5,
        )
        self.assertEqual(first, second)
        self.assertTrue(all(len(item["content_hash"]) == 64 for item in first))
        self.assertTrue(all(item["updated_at"].endswith("Z") for item in first))


if __name__ == "__main__":
    unittest.main()
