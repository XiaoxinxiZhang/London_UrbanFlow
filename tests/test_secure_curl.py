import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import secure_curl


class SystemProxyTests(unittest.TestCase):
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
    @mock.patch.object(secure_curl.urllib.request, "urlopen")
    def test_prefer_curl_skips_urllib(self, urlopen, curl):
        result = secure_curl.open_https("https://api.tfl.gov.uk/BikePoint", {}, prefer_curl=True)
        self.assertEqual(result, (200, b"[]"))
        urlopen.assert_not_called()
        curl.assert_called_once()


if __name__ == "__main__":
    unittest.main()
