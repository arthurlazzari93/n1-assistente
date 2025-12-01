---
title: Ajustar permissões e acesso em sites SharePoint
tags: [sharepoint, permissao, acesso negado, biblioteca, teams]
synonyms: [liberar acesso sharepoint, erro acesso negado, convidar usuario site, sharepoint permissions]
---

## Sintomas comuns
- Mensagem **Acesso negado** ao abrir site/biblioteca.
- Usuário consegue abrir via Teams, mas não pelo navegador ou Explorer.
- Pasta compartilhada some após mudança de equipe.

## Diagnóstico rápido
1. Identifique a URL do site e o grupo de segurança responsável (ex.: `Membros do Site XYZ`).
2. Em **Configurações > Permissões do site**, confirme se o usuário aparece no grupo correto.
3. Verifique se o acesso é herdado ou se a biblioteca usa permissões exclusivas.
4. Caso a chamada venha do Teams, abra **Gerenciar equipe > Membros** para conferir se o canal priva acesso.

## Passos de solução
1. Adicione o usuário ao grupo recomendado (preferir grupos M365/Teams, não permissões individuais).
2. Forçar herança: em **Configurações da biblioteca > Permissões**, selecione **Parar de herdar** apenas se realmente precisar bloquear.
3. Para liberar pasta específica, use o botão **Compartilhar** e selecione *As pessoas específicas* com e-mail corporativo.
4. Oriente o usuário a limpar o cache do navegador/OneDrive e reabrir o link copiado de **Abrir no SharePoint**.

## Critérios para escalar ao N2
- Sites com políticas de retenção/DLP que exigem alteração em nível de tenant.
- Erros `AADSTS` ou restrições de convidado externo.
- Ajustes que envolvem permissionamento em listas com workflows críticos.

## Referências
- https://learn.microsoft.com/sharepoint/default-permission-levels
- https://learn.microsoft.com/sharepoint/troubleshoot/sharing-and-permissions
