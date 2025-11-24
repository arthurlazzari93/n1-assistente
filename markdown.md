# Guia rápido de Markdown para a base de conhecimento

Este arquivo explica como escrever artigos em **Markdown** para a pasta `app/knowledge/`.  
Use este modelo ao criar novos conteúdos para o Assistente N1.

## Modelo de artigo

Use um cabeçalho (front‑matter) no início do arquivo:

```markdown
---
title: Título claro do procedimento
tags: [tag1, tag2, sistema, erro]
synonyms: [termo alternativo 1, termo 2, apelido]
---
```

Em seguida, a estrutura recomendada:

```markdown
## Pré-requisitos
- Liste aqui o que o usuário precisa antes de começar.

---

## Passo a passo

### 1) Nome da primeira etapa
1. Passo 1.
2. Passo 2.
3. Passo 3.

> Dica: use blocos de citação (`>`) para avisos, observações e notas importantes.

---

## Problemas comuns e soluções rápidas

**Descrição do problema A**  
- Solução / passos rápidos.

**Descrição do problema B**  
- Solução / passos rápidos.

---

## Referência
- Fonte interna, documento, link ou sistema de origem.
```

## Sintaxe básica de Markdown

- **Negrito**: `**texto**`
- *Itálico*: `*texto*`
- Listas não ordenadas:
  - Use `- item` ou `* item`
- Listas ordenadas:
  1. `1. primeiro`
  2. `2. segundo`
- Títulos:
  - `#` título principal
  - `##` seção
  - `###` subseção
- Código ou comandos:
  - Inline: `` `comando` ``
  - Bloco:
    ```markdown
    ```bash
    comando aqui
    ```
    ```

## Boas práticas de conteúdo

- Escreva sempre em **português claro** e objetivo.
- Use frases curtas e orientadas a ação (ex.: "Clique em...", "Verifique se...").
- Evite jargões desnecessários; quando usar, explique rapidamente.
- Cada arquivo deve tratar de um **assunto principal** (erro, sistema ou tarefa).
- Inclua **sinônimos** no front‑matter para ajudar na busca (ex.: nomes de erro, mensagens comuns).

## Notas sobre referências especiais

Nos artigos existentes em `app/knowledge/` você verá trechos como:

```markdown
:contentReference[oaicite:1]{index=1}
```

Esses marcadores são usados pelo sistema interno para ligar o texto a fontes ou capturas de tela.  
Se você não precisar deles, pode simplesmente **não usar** esse formato nos novos artigos.

## Checklist antes de salvar um novo artigo

- [ ] O título está claro e específico?
- [ ] Preencheu `tags` e `synonyms` no front‑matter?
- [ ] Os passos estão em ordem lógica e numerados?
- [ ] Há uma seção de problemas comuns, se fizer sentido?
- [ ] Não há dados sensíveis (senhas, tokens, IPs privados desnecessários)?

