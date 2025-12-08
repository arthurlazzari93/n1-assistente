import unittest
from unittest import mock

from app import session_movidesk


class ChatDrivenMovideskTests(unittest.TestCase):
    def test_build_summary_includes_subject_and_conversation(self):
        session = {"subject": "VPN", "last_intent": "vpn.access"}
        conversation = "Usuário: Olá\nBot: Siga estes passos"
        summary = session_movidesk.build_chat_session_summary(session, conversation)
        self.assertIn("VPN", summary)
        self.assertIn("Siga estes passos", summary)
        self.assertIn("orientação resolveu o problema", summary.lower())

    @mock.patch("app.session_movidesk.add_public_note")
    @mock.patch("app.session_movidesk.httpx.Client")
    @mock.patch("app.session_movidesk._get_token", return_value="token-123")
    def test_create_ticket_success(self, mock_token, mock_client_cls, mock_add_note):
        mock_client = mock.MagicMock()
        mock_resp = mock.MagicMock(status_code=201)
        mock_resp.json.return_value = {"id": 4321}
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        session = {"id": 99, "user_email": "user@test.com", "subject": "Impressora"}
        summary = "Resumo qualquer"
        ticket_id = session_movidesk.create_resolved_movidesk_ticket_from_session(session, summary)

        self.assertEqual(ticket_id, "4321")
        mock_client.post.assert_called_once()
        payload = mock_client.post.call_args.kwargs["json"]
        self.assertEqual(payload["clients"][0]["email"], "user@test.com")
        self.assertEqual(payload["status"], "Resolvido")
        mock_add_note.assert_called_once_with(4321, mock.ANY)


if __name__ == "__main__":
    unittest.main()
