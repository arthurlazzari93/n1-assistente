---
title: Instalação do Sisloc (Windows)
tags: [sisloc, instalação, erro de privacidade, smartscreen]
synonyms: [instalar sisloc, sisloccluster, limpar cache sisloc, excluir pasta SISLOC]
---

## Pré-requisitos
- Ter **VPN conectada** quando estiver fora da Tecnogera (acesso interno requerido). :contentReference[oaicite:1]{index=1}

---

## Passo a passo

### 1) Remover pastas antigas do Sisloc
1. Abra o **Explorador de Arquivos** e **exclua** a pasta **`SISLOC`** em **Documentos**. :contentReference[oaicite:2]{index=2}  
2. Pressione **`Windows + R`** → digite **`%TEMP%`** → **OK**.  
   Na pasta Temp, **exclua** a pasta **`SISLOCCI`**. :contentReference[oaicite:3]{index=3}

> Por que fazer isso? Remove restos de instalações anteriores que podem causar conflito.

---

### 2) Acessar o portal de instalação
1. Abra seu navegador (Chrome/Edge/Firefox).  
2. Acesse: **`https://10.246.0.22:60030/tecnogera`**  
   > Se estiver **fora da rede Tecnogera**, **mantenha a VPN ligada**. :contentReference[oaicite:4]{index=4}

---

### 3) Tratar o aviso de segurança do navegador
Ao acessar, pode aparecer **“Sua conexão não é particular”** (certificado interno).  
1. Clique em **Avançado** e **prossiga mesmo assim** para o endereço do servidor. :contentReference[oaicite:5]{index=5}

---

### 4) Iniciar a instalação do Sisloc
1. Na página do Sisloc, clique em **Instalar**. :contentReference[oaicite:6]{index=6}  
2. Quando o arquivo **`sislocinstall.exe`** for baixado, **clique para executar**. :contentReference[oaicite:7]{index=7}

---

### 5) Tratar o alerta do Windows SmartScreen (se aparecer)
1. Na janela **“O Windows protegeu o computador”**, clique em **Mais informações**.  
2. Clique em **Executar mesmo assim** para continuar a instalação. :contentReference[oaicite:8]{index=8}

---

### 6) Concluir a instalação
- Acompanhe a barra de progresso **“Estamos atualizando o Sisloc, por favor, aguarde…”** até finalizar. :contentReference[oaicite:9]{index=9}

---

## Validações finais
- Abra o **Sisloc** e verifique se inicia normalmente.  
- Realize um login de teste (se aplicável).  
- **Confirme se a sua solicitação foi atendida**.

---

## Problemas comuns e soluções rápidas

**O site não abre / fica indisponível**  
- Verifique se a **VPN está conectada** (fora da rede).  
- Tente novamente com **Chrome ou Edge**.  
- Confirme o endereço: `https://10.246.0.22:60030/tecnogera`. :contentReference[oaicite:10]{index=10}

**O navegador mostra “Conexão não é particular”**  
- Clique em **Avançado** → **Prosseguir** para o site (é esperado no ambiente interno). :contentReference[oaicite:11]{index=11}

**Windows bloqueou o arquivo**  
- Na tela do **SmartScreen**, clique **Mais informações** → **Executar mesmo assim**. :contentReference[oaicite:12]{index=12}

**Erro durante a instalação**  
- Feche qualquer janela do Sisloc aberta.  
- Refça a **limpeza das pastas** `SISLOC` (Documentos) e `SISLOCCI` (%TEMP%), depois **tente novamente**. :contentReference[oaicite:13]{index=13}

---

## Referência
Procedimento compilado a partir do documento “install sisloc.pdf” (capturas de tela e etapas). :contentReference[oaicite:14]{index=14}
