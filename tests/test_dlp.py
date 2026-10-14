import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from edm_ai import dlp  # noqa: E402


class Detect(unittest.TestCase):
    def test_rrn_requires_a_real_birth_date(self):
        self.assertEqual(dlp.scan("홍길동 900101-1234567"), {"rrn": 1})
        self.assertEqual(dlp.scan("외국인 850505-5234567"), {"alien": 1})
        self.assertEqual(dlp.scan("주문번호 991399-1234567"), {})                  # month 13: not a birth date
        self.assertEqual(dlp.scan("번호 123456-9234567"), {})                      # gender digit 9 is not an RRN

    def test_rrn_without_hyphen_needs_a_valid_checksum(self):
        self.assertEqual(dlp.scan("9001011234567"), {})                            # looks like a number, checksum fails
        body = "900101123456"
        w = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
        check = (11 - sum(int(a) * b for a, b in zip(body, w)) % 11) % 10
        self.assertEqual(dlp.scan(body + str(check)), {"rrn": 1})

    def test_card_numbers_use_luhn(self):
        self.assertEqual(dlp.scan("카드 4111 1111 1111 1111"), {"card": 1})
        self.assertEqual(dlp.scan("카드 4111-1111-1111-1112"), {})                  # fails Luhn
        self.assertEqual(dlp.scan("계약금액 480000000원"), {})

    def test_phone_email_passport(self):
        r = dlp.scan("연락처 010-1234-5678, 01098765432, kim@iccjeju.example 여권 M12345678")
        self.assertEqual(r, {"phone": 2, "email": 1, "passport": 1})

    def test_values_never_leave_the_module(self):
        info = dlp.describe(dlp.scan("900101-1234567 010-1234-5678"))
        self.assertNotIn("900101", str(info))
        self.assertEqual([i["kind"] for i in info], ["rrn", "phone"])               # high severity first
        self.assertEqual(dlp.summary({"rrn": 2}), "주민등록번호 2건")

    def test_neighbouring_digits_do_not_produce_matches(self):
        self.assertEqual(dlp.scan("19900101-12345678"), {})                        # 8 trailing digits: not an RRN
        self.assertEqual(dlp.scan("CT-2024-001 480,000,000 2026-12-20"), {})


if __name__ == "__main__":
    unittest.main()
