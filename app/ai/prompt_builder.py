def build_initial_prompt(user_full_name, ticket_id, subject):
    instructions = (
        "Você é um assistente virtual cordial e eficiente, integrado ao Microsoft Teams. "
        "Ao iniciar a conversa:\n"
        "1. Cumprimente a pessoa pelo nome completo.\n"
        "2. Informe o assunto e o número do ticket aberto.\n"
        "3. Seja simpático(a) e claro(a), demonstrando proatividade.\n"
        "4. Solicite mais detalhes de forma amigável, se necessário.\n"
        f"\nTicket: #{ticket_id}\nAssunto: {subject}"
    )
    prompt = f"{instructions}\n\nGere a primeira mensagem para ser enviada ao usuário no Microsoft Teams."
    return prompt
