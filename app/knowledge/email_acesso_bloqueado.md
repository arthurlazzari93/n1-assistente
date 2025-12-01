---
title: Desbloquear acesso ao e-mail corporativo
tags: [email, acesso bloqueado, conta desativada, licenca exchange, mfa]
synonyms: [conta email bloqueada, desbloquear mailbox, licenca expirou, mfa pendente email]
---

## Sintomas comuns
- Outlook/OWA pede senha repetidamente mesmo após credenciais corretas.
- Mensagem **Sua conta foi desabilitada** ou **Need admin approval**.
- MFA pendente após troca de celular.

## Diagnóstico rápido
1. Abra **Admin M365 > Usuários ativos** e confirme se a conta está `Habilitada` e com licença válida (Exchange/Business).
2. Verifique se há alertas de *Sign-in risk* em Azure AD Identity Protection.
3. Se MFA estiver ativo, veja se há métodos configurados (Telefone/App) e se houve reset recente.
4. Consulte logs para bloqueio por DLP/segurança (Defender).

## Passos de solução
1. Reabilite a conta ou atribua a licença correta (Office 365 E3/Business Standard).
2. Resete MFA: **Azure AD > Usuários > Autenticação multifator** > Requerer registro novamente.
3. Limpe credenciais salvas no Windows (`Gerenciador de Credenciais`) e peça novo login.
4. Caso o bloqueio seja por política de segurança, alinhe com o Security para liberar o sinal e registrar justificativa.

## Critérios para escalar ao N2
- Bloqueios por investigação de segurança ou compliance.
- Contas marcadas como comprometidas no Azure AD.
- Necessidade de scripts PowerShell para múltiplos usuários.

## Referências
- https://learn.microsoft.com/azure/active-directory/authentication/howto-mfa-userdevicesettings
- https://learn.microsoft.com/microsoft-365/admin/add-users/restore-user
