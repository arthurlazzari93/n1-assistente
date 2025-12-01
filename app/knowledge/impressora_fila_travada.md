---
title: Destravar fila e spooler de impressão
tags: [impressora, fila, spooler, trabalhos travados, limpar fila]
synonyms: [fila travada, spooler travou, impressora duplicando, cancelar impressao travada]
---

## Sintomas comuns
- Trabalhos ficam presos como **Em impressão** e não avançam.
- Impressora imprime várias vezes o mesmo documento.
- Serviço *Spooler de impressão* para com erro 1053.

## Diagnóstico rápido
1. Peça ao usuário o nome da fila/servidor (`\\print01\Financeiro`).
2. Confira se a fila está pausada (ícone cinza) no servidor.
3. Verifique se há trabalhos com tamanho 0 KB ou enviados por contas desativadas.
4. Determine se o problema é apenas em um usuário ou geral.

## Passos de solução
1. No servidor, abra **Gerenciamento de Impressão** > fila afetada > **Cancelar todos os documentos**.
2. Pare o serviço **Spooler de impressão**, apague `C:\Windows\System32\spool\PRINTERS\*` e inicie novamente.
3. Caso apenas um usuário seja afetado, remova/reinstale a fila no computador dele (printui /s /t2).
4. Atualize o driver caso o log de eventos mostre falha `Event 372/808` ligada ao pacote atual.
5. Oriente o usuário a evitar arquivos PDF corrompidos: imprimir como imagem ou converter para XPS.

## Critérios para escalar ao N2
- Spooler derruba todas as filas (evento 7031 recorrente).
- Impressoras com firmware antigo que reinicia continuamente.
- Necessidade de scripts para remoção em massa ou alterações no servidor de impressão.

## Referências
- https://learn.microsoft.com/troubleshoot/windows-server/print/how-to-delete-print-jobs
- https://learn.microsoft.com/windows-server/administration/print-server-management
