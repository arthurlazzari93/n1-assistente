---
title: Correções para sincronização do OneDrive for Business
tags: [onedrive, sharepoint, sincronizacao, arquivos, status amarelo]
synonyms: [onedrive travado, icone amarelo onedrive, processando alteracoes, erro sincronizacao onedrive]
---

## Sintomas comuns
- Ícone do OneDrive em amarelo com mensagem **Processando alterações** eterna.
- Arquivos ficam como *Pendente* ou duplicados (Nome-PC/CONFLITO).
- SharePoint aparece acessível via navegador, mas não sincroniza no Explorer.

## Diagnóstico rápido
1. Confirme se o usuário está logado com a conta corporativa correta (`Configurações > Conta`).
2. Abra `onedrive.exe /reset` para verificar se o cliente responde; se não, reinicie o Windows.
3. Valide espaço: OneDrive e disco local precisam de pelo menos 1 GB livre.
4. Cheque nomes de arquivos com caracteres especiais ou caminho > 300 caracteres.

## Passos de solução
1. Clique no ícone do OneDrive > **Ver mais** > **Pausar sincronização** (10 min) e reative.
2. Em **Configurações > Conta**, remova a biblioteca problemática e adicione novamente pelo link do SharePoint.
3. Execute `PowerShell` como admin e limpe cache: `Stop-Process -Name OneDrive -Force; Start-Process OneDrive`.
4. Caso apenas uma pasta falhe, habilite **Sincronização seletiva** deixando-a temporariamente fora e depois marque novamente.
5. Se o erro citar permissões, valide no SharePoint se o usuário tem ao menos *Leitura/Gravação*.

## Critérios para escalar ao N2
- Mais de 5.000 arquivos presos mesmo após reset/reconfiguração.
- Mensagens de erro `0x8004de40`, `0x8004def4` ou políticas de DLP/IRM.
- Todos os usuários de um site apresentando falha simultânea.

## Referências
- https://learn.microsoft.com/onedrive/resolve-sync-issues
- https://learn.microsoft.com/sharepoint/troubleshoot/issues/fix-sync-problems
