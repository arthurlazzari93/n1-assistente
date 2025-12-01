---
title: Instalação e implantação de impressoras corporativas
tags: [impressora, driver, instalacao, deploy, mapear impressora]
synonyms: [instalar impressora, adicionar impressora rede, printer driver, deploy impressora]
---

## Sintomas comuns
- Usuário não vê a impressora nas configurações do Windows.
- Driver solicita credenciais administrativas.
- Impressora instalada aponta para porta incorreta (WSD ao invés de TCP/IP).

## Diagnóstico rápido
1. Confirme se o equipamento está na VLAN correta e responde a `ping`.
2. Valide qual servidor de impressão hospeda a fila (ex.: `\\print01`).
3. Cheque se o pacote `.inf`/`.cab` está assinado e compatível com a versão do Windows.
4. Revise se a conta do usuário tem permissão de *Imprimir* na fila remota.

## Passos de solução
1. No Windows, abra **Configurações > Bluetooth e dispositivos > Impressoras** e remova tentativas antigas.
2. Baixe/extraia o driver oficial (x64) e instale via **Executar > printui /s /t2** (Adicionar > Com disco).
3. Configure porta TCP/IP fixa apontando para o IP do equipamento; desabilite descoberta WSD.
4. Se existir fila no servidor, utilize `\\print01\NOME-FILA` > botão direito > **Conectar**.
5. Para implantar a vários usuários, registre a fila em GPO ou intune (Policy Device > Printer).

## Critérios para escalar ao N2
- Driver inexistente para arquitetura atual ou assinatura inválida.
- Impressoras com firmware desatualizado exigindo acesso administrativo ao equipamento.
- Solicitações de publicação global (Azure AD/Universal Print).

## Referências
- https://learn.microsoft.com/windows-server/administration/windows-commands/printui
- https://learn.microsoft.com/windows/client-management/universal-print-deploy-printers
