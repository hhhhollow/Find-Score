import unittest
from unittest.mock import Mock

from grade_monitor.session import ApiError, JwxtSession


class SessionResponseTests(unittest.TestCase):
    def test_missing_rows_is_api_error(self) -> None:
        client = object.__new__(JwxtSession)
        client._post_json = Mock(return_value={"code": "0", "datas": {}})

        with self.assertRaisesRegex(ApiError, "rows"):
            client.fetch_all_grades()

    def test_empty_rows_is_valid_api_shape(self) -> None:
        client = object.__new__(JwxtSession)
        client._post_json = Mock(
            return_value={"code": "0", "datas": {"cxwdcj": {"rows": []}}},
        )

        self.assertEqual(client.fetch_all_grades(), [])

    def test_non_object_row_is_api_error(self) -> None:
        client = object.__new__(JwxtSession)
        client._post_json = Mock(
            return_value={"code": "0", "datas": {"cxwdcj": {"rows": ["bad"]}}},
        )

        with self.assertRaisesRegex(ApiError, "rows"):
            client.fetch_all_grades()

    def test_malformed_details_is_api_error(self) -> None:
        client = object.__new__(JwxtSession)
        client._post_json = Mock(
            return_value={
                "code": "0",
                "datas": {"details": {"itemScores": ["bad"]}},
            },
        )

        with self.assertRaisesRegex(ApiError, "itemScores"):
            client.fetch_grade_details("wid")


if __name__ == "__main__":
    unittest.main()
