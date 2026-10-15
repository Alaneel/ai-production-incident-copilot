import unittest

from incident_mcp.security import ApprovalReceiptVerifier, AuthorizationError


class ApprovalReceiptTests(unittest.TestCase):
    def test_receipt_is_bound_to_exact_operation(self) -> None:
        verifier = ApprovalReceiptVerifier("test-secret")
        receipt = verifier.issue(
            incident_id="INC-2847",
            actor="oncall@example.com",
            tool="rollback_release",
            service="payment-service",
            namespace="prod-sg",
            target="141",
        )
        payload = verifier.verify(
            receipt,
            tool="rollback_release",
            service="payment-service",
            namespace="prod-sg",
            target="141",
        )
        self.assertEqual(payload["actor"], "oncall@example.com")

        with self.assertRaises(AuthorizationError):
            verifier.verify(
                receipt,
                tool="rollback_release",
                service="payment-service",
                namespace="prod-sg",
                target="140",
            )

    def test_expired_receipt_is_rejected(self) -> None:
        verifier = ApprovalReceiptVerifier("test-secret")
        receipt = verifier.issue(
            incident_id="INC-2847",
            actor="oncall@example.com",
            tool="restart_deployment",
            service="payment-service",
            namespace="prod-sg",
            target="prod-sg",
            ttl_seconds=-1,
        )
        with self.assertRaises(AuthorizationError):
            verifier.verify(
                receipt,
                tool="restart_deployment",
                service="payment-service",
                namespace="prod-sg",
                target="prod-sg",
            )


if __name__ == "__main__":
    unittest.main()
