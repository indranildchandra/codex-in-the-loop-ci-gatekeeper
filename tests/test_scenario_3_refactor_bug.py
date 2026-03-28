import unittest

from orders import OrderService


class RefactorBugTests(unittest.TestCase):
    def test_order_total_with_percentage_tax(self) -> None:
        service = OrderService()

        order = service.create_order(100)

        self.assertEqual(order["total"], 110)


if __name__ == "__main__":
    unittest.main()
