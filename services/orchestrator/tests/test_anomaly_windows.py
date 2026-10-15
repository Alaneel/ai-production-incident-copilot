import unittest
from datetime import UTC, datetime, timedelta

from incident_copilot.anomaly_windows import detect_anomaly_windows
from incident_copilot.models import Evidence, EvidenceKind


class AnomalyWindowTests(unittest.TestCase):
    now = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)

    def metric(self, values, name="request_latency_p95", timestamps=None):
        times = timestamps or [self.now - timedelta(minutes=len(values)-1-index)
                               for index in range(len(values))]
        return Evidence(
            kind=EvidenceKind.METRIC, source="prometheus", title=name,
            observation="sampled signal", observed_at=self.now,
            service="payment-service", query=name, score=0.9,
            attributes={"series": [{"timestamp": t.isoformat(), "value": v}
                                   for t, v in zip(times, values)]},
        )

    def test_detects_actual_first_and_last_abnormal_samples(self):
        item = self.metric([200, 250, 1800, 2200, 300])
        windows = detect_anomaly_windows([item], now=self.now)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["start"], (self.now - timedelta(minutes=2)).isoformat())
        self.assertEqual(windows[0]["end"], (self.now - timedelta(minutes=1)).isoformat())
        self.assertEqual(windows[0]["sample_count"], 2)
        self.assertEqual(windows[0]["evidence_id"], item.id)
        self.assertEqual(windows[0]["threshold"], 1000)
        self.assertFalse(windows[0]["left_censored"])

    def test_separate_bursts_and_single_spikes_are_not_merged(self):
        windows = detect_anomaly_windows([self.metric([2000, 2200, 200, 2500, 2600])], now=self.now)
        self.assertEqual(len(windows), 2)
        self.assertTrue(windows[0]["left_censored"])
        self.assertFalse(windows[1]["left_censored"])
        self.assertEqual(detect_anomaly_windows([self.metric([200, 2500, 200])], now=self.now), [])

    def test_invalid_future_and_naive_samples_do_not_create_windows(self):
        item = self.metric([200, 200])
        item.attributes["series"].extend([
            {"timestamp": "broken", "value": 2200},
            {"timestamp": self.now.isoformat(), "value": "NaN"},
            {"timestamp": self.now.isoformat(), "value": True},
            {"timestamp": self.now.replace(tzinfo=None).isoformat(), "value": 2200},
            {"timestamp": (self.now + timedelta(minutes=1)).isoformat(), "value": 2200},
            {"timestamp": (self.now - timedelta(hours=1)).isoformat(), "value": 2200},
        ])
        self.assertEqual(detect_anomaly_windows([item], now=self.now), [])

    def test_duplicates_and_unordered_samples_do_not_inflate_support(self):
        item = self.metric([200, 2200, 2300])
        item.attributes["series"].reverse()
        windows = detect_anomaly_windows([item], now=self.now)
        self.assertEqual(windows[0]["sample_count"], 2)
        item.attributes["series"] = [{"timestamp": self.now.isoformat(), "value": 2200}] * 5
        self.assertEqual(detect_anomaly_windows([item], now=self.now), [])

    def test_missing_scrapes_do_not_bridge_anomaly_windows(self):
        item = self.metric([2200, 2500], timestamps=[self.now - timedelta(minutes=10), self.now])
        self.assertEqual(detect_anomaly_windows([item], now=self.now), [])

    def test_memory_growth_uses_the_shared_trend_rule(self):
        mib = 1024 * 1024
        item = self.metric([128*mib, 160*mib, 192*mib], name="memory_working_set")
        windows = detect_anomaly_windows([item], now=self.now)
        self.assertEqual(windows[0]["criterion"], "sustained_memory_growth")
        stable = self.metric([512*mib]*5, name="memory_working_set")
        self.assertEqual(detect_anomaly_windows([stable], now=self.now), [])
