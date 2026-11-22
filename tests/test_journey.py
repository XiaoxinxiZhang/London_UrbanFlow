"""Journey selection uses the current dock snapshot and actual routed legs."""
from datetime import datetime, timezone
from io import BytesIO
import json
import sqlite3
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from city_analysis import distance
from journey import DAQI, JourneyPlanner, OpenRouteService, RoutingUnavailable, estimate

UTC = timezone.utc
START = [-.1500, 51.5100]
END = [-.1100, 51.5100]


def dock(sid, point, bikes, spaces):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": point},
            "properties": {"id": sid, "name": sid, "bikes": bikes, "empty_docks": spaces}}


class Analysis:
    def __init__(self, bikes, stale=False):
        self.bikes = bikes
        self.stale = stale

    def connect(self):
        return sqlite3.connect(":memory:")

    def traffic(self, db, kind):
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        if kind == "bikes":
            return self.bikes, {"captured_at": stamp, "stale": self.stale, "gap": False}
        return [], {"captured_at": stamp, "stale": False, "gap": False}

    def air_features(self, db, params, start, end, cursor):
        return [], []


class Router:
    def __init__(self, fail=False):
        self.fail = fail
        self.matrix_calls = []
        self.directions_calls = []

    def matrix(self, profile, sources, destinations):
        self.matrix_calls.append((profile, sources, destinations))
        distances = [[distance(a, b)*1.1 for b in destinations] for a in sources]
        speed = 4 if profile == "cycling-regular" else 1.3
        durations = [[meters/speed for meters in row] for row in distances]
        return distances, durations

    def directions(self, profile, start, end):
        if self.fail:
            raise RoutingUnavailable("route service down")
        self.directions_calls.append((profile, start, end))
        meters = distance(start, end)*1.1
        return {"geometry": {"type": "LineString", "coordinates": [start, end]},
                "distance_m": meters, "duration_s": meters/(4 if profile == "cycling-regular" else 1.3),
                "steps": [{"instruction": "继续前行"}]}


class FixedDAQI:
    def current(self):
        return {"status": "fresh", "index": 3, "band": "低"}


class JourneyTests(unittest.TestCase):
    def test_real_transport_uses_verified_curl_post_and_geojson_accept(self):
        payload = {"features": [{"geometry": {"type": "LineString", "coordinates": [START, END]},
                   "properties": {"summary": {"distance": 3000, "duration": 2000}}}]}
        with patch("journey.curl_https", return_value=(200, json.dumps(payload).encode())) as transport:
            result = OpenRouteService(key="test-only-key").directions("foot-walking", START, END)
            self.assertEqual(result["distance_m"], 3000)
            args, kwargs = transport.call_args
            self.assertIn("application/geo+json", args[1]["Accept"])
            self.assertEqual(json.loads(kwargs["data"])["coordinates"], [START, END])
        for status, message in [(403, "认证失败"), (429, "频率已达上限")]:
            with self.subTest(status=status), patch("journey.curl_https", return_value=(status, b"{}")), \
                    patch("journey.urllib_https") as fallback:
                with self.assertRaisesRegex(RoutingUnavailable, message):
                    OpenRouteService(key="test-only-key").directions("foot-walking", START, END)
                fallback.assert_not_called()

    def test_ors_transport_failure_retries_the_same_request_once(self):
        with patch("journey.curl_https", side_effect=OSError("test-only-key")) as curl, \
                patch("journey.urllib_https", return_value=(200, b'{"distances":[[100]],"durations":[[80]]}')) as fallback, \
                self.assertLogs("journey", level="WARNING") as logs:
            result = OpenRouteService(key="test-only-key").matrix("foot-walking", [START], [END])
        self.assertEqual(result, ([[100]], [[80]]))
        curl.assert_called_once()
        fallback.assert_called_once_with(*curl.call_args.args, timeout=8, data=curl.call_args.kwargs["data"])
        self.assertNotIn("test-only-key", "\n".join(logs.output))

    def test_ors_reports_timeout_network_and_invalid_response_separately(self):
        router = OpenRouteService(key="test-only-key")
        for reason, message in [(TimeoutError(), "连接超时"), (ConnectionResetError(), "网络连接失败")]:
            with self.subTest(reason=type(reason).__name__), \
                    patch("journey.curl_https", side_effect=TimeoutError), \
                    patch("journey.urllib_https", side_effect=URLError(reason)) as fallback, \
                    self.assertLogs("journey", level="WARNING"):
                with self.assertRaisesRegex(RoutingUnavailable, message):
                    router.directions("cycling-regular", START, END)
                fallback.assert_called_once()
        with patch("journey.curl_https", return_value=(200, b"<html>invalid</html>")), \
                patch("journey.urllib_https") as fallback, self.assertLogs("journey", level="WARNING"):
            with self.assertRaisesRegex(RoutingUnavailable, "无法读取的响应"):
                router.directions("foot-walking", START, END)
            fallback.assert_not_called()

    def setUp(self):
        self.bikes = [dock("pickup", [-.149, 51.510], 4, 0),
                      dock("dropoff", [-.111, 51.510], 0, 6),
                      dock("empty", [-.1495, 51.510], 0, 9),
                      dock("full", [-.1105, 51.510], 7, 0)]
        self.payload = {"start": START, "end": END, "mode": "shared_bike", "max_walk_m": 1000}

    def test_complete_pickup_cycle_return_and_alternative_selection(self):
        router = Router()
        planner = JourneyPlanner(Analysis(self.bikes), router, FixedDAQI())
        result = planner.plan(self.payload)
        self.assertEqual([leg["kind"] for leg in result["legs"]],
                         ["walk_to_bike", "cycle", "walk_to_destination"])
        self.assertEqual(result["alternatives"][0]["pickup"]["id"], "pickup")
        self.assertEqual(result["alternatives"][0]["dropoff"]["id"], "dropoff")
        self.assertEqual([call[0] for call in router.matrix_calls],
                         ["foot-walking", "foot-walking", "cycling-regular"])
        self.assertEqual(result["context"]["daqi"]["index"], 3)
        with self.assertRaisesRegex(ValueError, "不可用"):
            planner.plan({**self.payload, "pickup_id": "empty", "dropoff_id": "dropoff"})

    def test_stale_bikes_use_direct_walk_without_claiming_docks(self):
        router = Router()
        result = JourneyPlanner(Analysis(self.bikes, stale=True), router, FixedDAQI()).plan(self.payload)
        self.assertEqual([leg["kind"] for leg in result["legs"]], ["walk"])
        self.assertEqual(result["alternatives"], [])
        self.assertIn("15 分钟", result["message"])

    def test_no_docks_and_routing_failure_are_explicit(self):
        result = JourneyPlanner(Analysis([]), Router(fail=True), FixedDAQI()).plan(self.payload)
        self.assertEqual(result["legs"], [])
        self.assertIn("无可取车辆", result["message"])
        self.assertEqual(result["routing_error"], "route service down")

    def test_idw_requires_two_fresh_same_source_observations(self):
        def air(lon, value, stale=False):
            return {"geometry": {"coordinates": [lon, 51.51]},
                    "properties": {"value": value, "stale": stale, "unit": "µg/m³",
                                   "observed_at": "2026-11-18T12:00:00+00:00"}}
        self.assertEqual(estimate([air(-.149, 10)], START)["status"], "insufficient")
        found = estimate([air(-.149, 10), air(-.151, 20), air(-.1505, 999, True)], START)
        self.assertEqual(found["status"], "estimated")
        self.assertEqual(found["station_count"], 2)
        self.assertAlmostEqual(found["value"], 15, delta=.2)

    def test_official_greater_london_daqi_and_stale_contract(self):
        feed = b"<rss><channel><item><title>Greater London</title><description>Current pollution level is Low at index 3</description><pubDate>Wed, 18 Nov 2026 15:00:00 +0000</pubDate></item></channel></rss>"
        class Response(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        current = DAQI(opener=lambda url, timeout: Response(feed)).current()
        self.assertEqual(current["index"], 3)
        self.assertEqual(current["band"], "低")
        self.assertIn(current["status"], ("fresh", "stale"))

    def test_ors_matrix_uses_string_location_indices(self):
        class Response(BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        requests = []
        def opener(request, timeout):
            requests.append(request)
            return Response(b'{"distances":[[100]],"durations":[[80]]}')
        router = OpenRouteService(key="test-key", opener=opener)
        self.assertEqual(router.matrix("foot-walking", [START], [END]), ([[100]], [[80]]))
        self.assertEqual(requests[0].full_url,
                         "https://api.heigit.org/openrouteservice/v2/matrix/foot-walking")
        body = json.loads(requests[0].data)
        self.assertEqual(body["sources"], ["0"])
        self.assertEqual(body["destinations"], ["1"])


if __name__ == "__main__":
    unittest.main()
