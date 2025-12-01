import unittest

from app.ai import triage_agent


class IntentHeuristicTests(unittest.TestCase):
    def setUp(self):
        # Garante que os testes usem apenas o fallback heurístico
        triage_agent._CLIENT = None

    def test_onedrive_sync_is_detected(self):
        text = "OneDrive com ícone amarelo e sincronização pendente de arquivos grandes."
        result = triage_agent.classify_intent(text)
        self.assertEqual(result["intent"], "onedrive.sync_issue")

    def test_printer_queue_issue_is_detected(self):
        text = "Fila da impressora do financeiro travada, spooler reinicia e não limpa os trabalhos."
        result = triage_agent.classify_intent(text)
        self.assertEqual(result["intent"], "printer.queue_stuck")

    def test_sharepoint_permission_issue_is_detected(self):
        text = "Usuário sem acesso a biblioteca do SharePoint, pede liberação de permissões."
        result = triage_agent.classify_intent(text)
        self.assertEqual(result["intent"], "sharepoint.permission_issue")

    def test_email_access_blocked_intent(self):
        text = "Outlook pede senha e diz que a caixa de e-mail está bloqueada por MFA."
        result = triage_agent.classify_intent(text)
        self.assertEqual(result["intent"], "email.access_blocked")


if __name__ == "__main__":
    unittest.main()
