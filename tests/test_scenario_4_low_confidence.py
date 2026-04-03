import unittest

from delivery_window import promised_days


class LowConfidenceScenarioTests(unittest.TestCase):
    def test_promised_days_rounds_up_partial_window(self):
        self.assertEqual(promised_days(501), 2)


if __name__ == "__main__":
    unittest.main()
