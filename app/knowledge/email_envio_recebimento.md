---
title: Problemas de envio/recebimento no e-mail corporativo
tags: [email, outlook, envio, recebimento, bounce, fila saida]
synonyms: [email nao envia, email nao recebe, mensagem volta, caindo na quarentena, stuck outbox]
---

## Sintomas comuns
- Mensagens ficam na **Caixa de saída** sem sair.
- E-mails retornam com `Undeliverable`, `550`, `552` ou `Spam blocked`.
- Apenas mensagens externas falham; internas funcionam.

## Diagnóstico rápido
1. Valide conexão com Exchange/Office 365: `Ctrl + Clique` no ícone Outlook > **Status da conexão**.
2. Teste via **Outlook Web**; se funcionar, problema está no perfil local.
3. Confira se há regras de transporte/quarentena (Security Center) segurando o domínio.
4. Peça o bounce completo para identificar código e servidor que rejeitou.

## Passos de solução
1. Se o problema for local, recrie o perfil: Painel de Controle > Correio > Mostrar perfis > Adicionar.
2. Limpe a pasta **Itens Enviados/Outbox** e reduza anexos maiores que 20 MB (use OneDrive link).
3. Caso apenas destinatários externos falhem, valide se o domínio está listado em bloqueios ou listas permitidas.
4. Para mensagens em quarentena, libere via **Defender for Office 365** e marque como *Allowed*.
5. Oriente usuário a desabilitar antivírus complementar que intercepta SMTP.

## Critérios para escalar ao N2
- Códigos 5xx persistentes mesmo após testes no OWA.
- Suspeita de bloqueio por reputação/DNS (precisa de ajuste em registros SPF/DKIM).
- Quarentena massiva envolvendo múltiplos remetentes.

## Referências
- https://learn.microsoft.com/exchange/mail-flow-best-practices/non-delivery-reports-in-exchange-online
- https://learn.microsoft.com/microsoft-365/security/office-365-security/quarantine-email-messages
