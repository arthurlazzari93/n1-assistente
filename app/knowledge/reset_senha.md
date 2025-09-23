---
title: Redefinir senha de usuário de domínio (AD) no Windows
tags: [senha, windows, ad, active directory, reset]
synonyms: [reset senha, redefinir senha, alterar senha ad, trocar senha windows]
---

# Alterar senha (usuário lembra a senha atual)
1. Faça login normalmente no Windows.
2. Pressione **Ctrl + Alt + Del**.
3. Clique em **Alterar uma senha**.
4. Digite:
   - **Senha atual**
   - **Nova senha**
   - **Confirmar nova senha**
5. Pressione **Enter** para concluir.


# Esqueceu a senha (não lembra a atual)
1. Entre em contato com o **time de TI / Administrador do AD**.
2. O administrador acessará o **Active Directory Users and Computers (ADUC)** no servidor.
3. Localizará sua conta e usará a opção **Reset Password**.
4. Uma **senha temporária** será definida e marcada para troca no próximo logon.
5. Reinicie o notebook, conecte-se à rede corporativa (cabo ou VPN) e faça login com a senha temporária.
6. Na primeira entrada, será solicitado criar uma **nova senha pessoal**.


# Observações
- É necessário estar conectado à **rede corporativa** ou à **VPN** para sincronizar a senha com o servidor.
- A nova senha deve obedecer às **políticas de complexidade do AD** (ex.: tamanho mínimo, letras maiúsculas, números e símbolos).
- Caso a conta fique **bloqueada por tentativas erradas**, apenas o **TI pode desbloquear**.
Confirme se a sua solicitação foi atendida