from django.test import SimpleTestCase

from .barcodes import (
    calculate_ean13_check_digit,
    generate_ean13,
    normalize_ean13,
    validate_ean13,
)


class Ean13NormalizationTests(SimpleTestCase):
    def test_normalize_ean13_removes_spaces_and_hyphens(self):
        self.assertEqual(
            normalize_ean13("4006 3813-3393 1"),
            "4006381333931",
        )

    def test_normalize_ean13_rejects_letters_and_other_characters(self):
        for value in ("40063813339A1", "400638133393/", "400638133393\t1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_ean13(value)

    def test_normalize_ean13_rejects_invalid_lengths(self):
        for value in ("400638133393", "40063813339310"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_ean13(value)


class Ean13ValidationTests(SimpleTestCase):
    def test_calculate_ean13_check_digit_for_known_code(self):
        self.assertEqual(
            calculate_ean13_check_digit("400638133393"),
            "1",
        )

    def test_validate_ean13_returns_normalized_known_code(self):
        self.assertEqual(
            validate_ean13("4006-3813-3393-1"),
            "4006381333931",
        )

    def test_validate_ean13_rejects_invalid_check_digit(self):
        with self.assertRaisesRegex(ValueError, "verificador"):
            validate_ean13("4006381333932")


class Ean13GenerationTests(SimpleTestCase):
    def test_generate_ean13_returns_valid_internal_code_without_collision(self):
        used_codes = {"2000000000008"}

        generated = generate_ean13(used_codes)

        self.assertTrue(generated.startswith("200"))
        self.assertEqual(validate_ean13(generated), generated)
        self.assertNotIn(generated, used_codes)
