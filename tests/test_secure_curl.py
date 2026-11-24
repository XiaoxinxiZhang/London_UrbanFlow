import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import secure_curl


class SystemProxyTests(unittest.TestCase):
    @mock.patch.object(secure_curl.urllib.request, "proxy_bypass", return_value=False)
    @mock.patch.object(secure_curl.urllib.request, "getproxies", return_value={})
    def test_project_proxy_used_when_scheduled_process_has_no_proxy_env(self, _proxies, _bypass):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbound_proxy.txt"
            path.write_text("http://127.0.0.1:17898\n", encoding="utf-8")
            with mock.patch.object(secure_curl, "PROJECT_PROXY_FILE", path):
                self.assertEqual(secure_curl._system_proxy_for("https://api.tfl.gov.uk/BikePoint"),
                                 "http://127.0.0.1:17898")

    @mock.patch.object(secure_curl.urllib.request, "proxy_bypass", return_value=False)
    @mock.patch.object(secure_curl.urllib.request, "getproxies",
                       return_value={"https": "127.0.0.1:7890"})
    def test_windows_proxy_is_normalized_for_curl(self, _proxies, _bypass):
        self.assertEqual(secure_curl._system_proxy_for("https://api.tfl.gov.uk/BikePoint"),
                         "http://127.0.0.1:7890")

    @mock.patch.object(secure_curl.urllib.request, "proxy_bypass", return_value=False)
    @mock.patch.object(secure_curl.urllib.request, "getproxies",
                       return_value={"https": "http://name:secret@proxy.example:8080"})
    def test_proxy_credentials_are_not_put_on_process_command_line(self, _proxies, _bypass):
        self.assertIsNone(secure_curl._system_proxy_for("https://api.tfl.gov.uk/BikePoint"))

    @mock.patch.object(secure_curl, "curl_https", return_value=(200, b"[]"))
    @mock.patch.object(secure_curl, "urllib_https")
    def test_prefer_curl_skips_urllib(self, python_https, curl):
        result = secure_curl.open_https("https://api.tfl.gov.uk/BikePoint", {}, prefer_curl=True)
        self.assertEqual(result, (200, b"[]"))
        python_https.assert_not_called()
        curl.assert_called_once()

    @mock.patch.object(secure_curl, "curl_https", side_effect=TimeoutError)
    @mock.patch.object(secure_curl, "urllib_https", return_value=(200, b"[]"))
    def test_prefer_curl_falls_back_to_python_on_transport_failure(self, python_https, curl):
        url = "https://api.tfl.gov.uk/BikePoint"
        self.assertEqual(secure_curl.open_https(url, {}, prefer_curl=True), (200, b"[]"))
        curl.assert_called_once_with(url, {}, timeout=25)
        python_https.assert_called_once_with(url, {}, timeout=8)

    @mock.patch.object(secure_curl.urllib.request, "build_opener")
    def test_python_https_keeps_credentials_on_original_host(self, build_opener):
        response = build_opener.return_value.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b"{}"
        url = "https://api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson"
        result = secure_curl.urllib_https(url, {"Authorization": "test-only-key"}, data=b"{}")
        self.assertEqual(result, (200, b"{}"))
        request = build_opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, url)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "test-only-key")
        redirect_handler = build_opener.call_args.args[0]
        self.assertIsNone(redirect_handler.redirect_request(request, None, 302, "Found", {}, "https://example.com"))


if __name__ == "__main__":
    unittest.main()
