import base64
import unittest

from grade_monitor.crypto import encrypt_sm2

TEST_PUB_KEY = "BN6l0mvj55Fvvas/vgLD8/xYTA9Ni1+zsKivNpJJ1Scw7th3Wr3ZH/+GnF/rdULFRQR7Zs05t9Zz7z5MbQlvnm0="


class CryptoTests(unittest.TestCase):
    def test_encrypt_sm2_valid(self) -> None:
        cipher_b64 = encrypt_sm2("hello_bistu", TEST_PUB_KEY)
        self.assertIsInstance(cipher_b64, str)
        raw = base64.b64decode(cipher_b64)
        # C1 (64 bytes) + C3 (32 bytes) + len("hello_bistu") (11 bytes) = 107 bytes
        self.assertEqual(len(raw), 64 + 32 + 11)

    def test_encrypt_empty_inputs_raise_value_error(self) -> None:
        with self.assertRaises(ValueError):
            encrypt_sm2("", TEST_PUB_KEY)
        with self.assertRaises(ValueError):
            encrypt_sm2("password", "")

    def test_invalid_key_length_raises(self) -> None:
        with self.assertRaises(ValueError):
            encrypt_sm2("password", "YWJj")  # "abc" -> 3 bytes


if __name__ == "__main__":
    unittest.main()
