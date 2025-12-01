import unittest

try:
    from app.bot import format_ticket_listing, resolve_ticket_choice, build_status_message  # type: ignore
except Exception:  # pragma: no cover
    format_ticket_listing = resolve_ticket_choice = build_status_message = None  # type: ignore


@unittest.skipIf(format_ticket_listing is None, "Dependências do bot não disponíveis")
class BotHelperTests(unittest.TestCase):
    def test_format_ticket_listing(self):
        tickets = [
            {"ticket_id": 101, "subject": "OneDrive", "last_seen_at": "2025-12-01T10:00:00Z"},
            {"ticket_id": 202, "subject": "Impressora"},
        ]
        output = format_ticket_listing(tickets)
        self.assertIn("1) #101", output)
        self.assertIn("2) #202", output)

    def test_resolve_ticket_choice(self):
        tickets = [{"ticket_id": 10}, {"ticket_id": 20}]
        self.assertEqual(resolve_ticket_choice("1", tickets)["ticket_id"], 10)
        self.assertEqual(resolve_ticket_choice("20", tickets)["ticket_id"], 20)
        self.assertIsNone(resolve_ticket_choice("5", tickets))

    def test_build_status_message(self):
        ticket = {
            "ticket_id": 55,
            "subject": "VPN",
            "n1_reason": "Teste",
            "teams_notified": 1,
            "allowed": 1,
        }
        msg = build_status_message(ticket)
        self.assertIn("Ticket #55", msg)
        self.assertIn("Teste", msg)


if __name__ == "__main__":
    unittest.main()
