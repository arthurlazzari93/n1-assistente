---
title: Acesso a sistemas internos (ERP/CRM/SISLOC)
tags: [erp, crm, sisloc, protheus, sap, acesso sistema, perfil]
synonyms: [acesso erp bloqueado, liberar perfil protheus, usuario sem perfil sap, erro sisloc login]
---

## Sintomas comuns
- Usuário recebe mensagem **Usuário sem permissão/perfil** ao abrir ERP.
- Login funciona fora da VPN, mas dentro retorna erro de licença.
- Sistemas como SISLOC/Protheus pedem atualização de módulo não instalada.

## Diagnóstico rápido
1. Valide se a VPN está ativa (IPs internos respondendo ping).
2. Confira se o usuário está no grupo correto do AD (ex.: `ERP-FINANCEIRO`, `CRM-VENDAS`).
3. Pergunte qual ambiente falhou (produção, homologação, mobile) e o horário aproximado.
4. Revise se há alertas abertos na monitoração do sistema (Zabbix/Statuspage interno).

## Passos de solução
1. Ajuste grupos no AD conforme matriz de acesso e peça ao usuário para relogar (Ctrl+Alt+Del > Trocar usuário).
2. Para SISLOC, rode o atualizador (`SislocUpdater.exe`) como administrador e valide versões mínimas.
3. Nos sistemas Web (CRM/BI), limpe cache do navegador e remova extensões bloqueadoras.
4. Se a mensagem citar licença ou limite de sessões, finalize conexões zumbis via console do aplicativo ou peça ao N2 liberar.

## Critérios para escalar ao N2
- Problemas em múltiplos usuários simultâneos (possível queda do serviço).
- Solicitacão de criação/alteração de perfis complexos (novos módulos, workflows).
- Falhas ligadas a integrações com bancos/SEFAZ que exigem intervenção do fornecedor.

## Referências
- Manual interno de acessos Tecnogera (SharePoint > Operações > Matriz de Perfis).
- Documentação SISLOC https://ajuda.sisloc.com.br/
