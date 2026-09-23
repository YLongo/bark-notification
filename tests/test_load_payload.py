"""Tests for _load_payload (and the non-dict coercion helper)."""
import contextlib
import io
import json
import unittest

from bark_notification import _coerce_payload_dict, _load_payload


class CoercePayloadDictTests(unittest.TestCase):
    def test_passes_dict_through(self):
        self.assertEqual(_coerce_payload_dict({"a": 1}), {"a": 1})

    def test_empty_dict(self):
        self.assertEqual(_coerce_payload_dict({}), {})

    def test_rejects_list_with_warning(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.assertEqual(_coerce_payload_dict([1, 2, 3]), {})
        self.assertIn("list", buf.getvalue())
        self.assertIn("bark-notify", buf.getvalue())

    def test_rejects_int_with_warning(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.assertEqual(_coerce_payload_dict(42), {})
        self.assertIn("int", buf.getvalue())

    def test_rejects_string_with_warning(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.assertEqual(_coerce_payload_dict("hello"), {})
        self.assertIn("str", buf.getvalue())

    def test_rejects_null_with_warning(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.assertEqual(_coerce_payload_dict(None), {})
        self.assertIn("NoneType", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
