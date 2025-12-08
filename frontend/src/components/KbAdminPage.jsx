import React, { useEffect, useMemo, useState } from "react";

const initialFormState = {
  slug: "",
  titulo: "",
  tagsInput: "",
  ativo: true,
  conteudo_markdown: ""
};

const emptyStats = { docs: 0, chunks: 0, avgdl: 0 };

function KbAdminPage() {
  const [articles, setArticles] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [onlyActive, setOnlyActive] = useState(false);
  const [formState, setFormState] = useState(initialFormState);
  const [formMode, setFormMode] = useState("create"); // create | edit
  const [formVisible, setFormVisible] = useState(false);
  const [formSaving, setFormSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [toast, setToast] = useState(null);
  const [reindexLoading, setReindexLoading] = useState(false);
  const [reindexStats, setReindexStats] = useState(emptyStats);

  useEffect(() => {
    fetchArticles();
  }, []);

  const filteredArticles = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    return articles.filter((article) => {
      const text = `${article.slug} ${article.titulo}`.toLowerCase();
      const matchesSearch = term ? text.includes(term) : true;
      const matchesActive = onlyActive ? article.ativo : true;
      return matchesSearch && matchesActive;
    });
  }, [articles, searchTerm, onlyActive]);

  const fetchArticles = async () => {
    setListLoading(true);
    setListError("");
    try {
      const res = await fetch("/debug/kb/articles");
      if (!res.ok) {
        throw new Error(`Falha ao listar artigos (${res.status})`);
      }
      const data = await res.json();
      setArticles(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error(err);
      setListError("Não foi possível carregar os artigos. Verifique o backend.");
    } finally {
      setListLoading(false);
    }
  };

  const openCreateModal = () => {
    setFormMode("create");
    setFormState(initialFormState);
    setFormError("");
    setFormVisible(true);
  };

  const openEditModal = async (slug) => {
    setFormMode("edit");
    setFormVisible(true);
    setFormError("");
    setFormSaving(true);
    try {
      const res = await fetch(`/debug/kb/articles/${slug}`);
      if (!res.ok) {
        throw new Error(`Falha ao carregar artigo (${res.status})`);
      }
      const data = await res.json();
      setFormState({
        slug: data.slug,
        titulo: data.titulo,
        tagsInput: Array.isArray(data.tags) ? data.tags.join(", ") : "",
        ativo: Boolean(data.ativo),
        conteudo_markdown: data.conteudo_markdown || ""
      });
    } catch (err) {
      console.error(err);
      setFormError("Não foi possível carregar o artigo selecionado.");
    } finally {
      setFormSaving(false);
    }
  };

  const closeModal = () => {
    if (formSaving) return;
    setFormVisible(false);
    setFormState(initialFormState);
    setFormError("");
  };

  const parseTags = (input) =>
    input
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean);

  const handleFormChange = (field, value) => {
    setFormState((prev) => ({ ...prev, [field]: value }));
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setFormSaving(true);
    setFormError("");
    const payload = {
      slug: formState.slug.trim().toLowerCase(),
      titulo: formState.titulo.trim(),
      tags: parseTags(formState.tagsInput || ""),
      ativo: Boolean(formState.ativo),
      conteudo_markdown: formState.conteudo_markdown || ""
    };
    if (!payload.slug || !payload.titulo) {
      setFormError("Slug e título são obrigatórios.");
      setFormSaving(false);
      return;
    }
    try {
      const endpoint = formMode === "create" ? "/debug/kb/articles" : `/debug/kb/articles/${payload.slug}`;
      const method = formMode === "create" ? "POST" : "PUT";
      const res = await fetch(endpoint, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Falha ao salvar artigo (${res.status})`);
      }
      await fetchArticles();
      showToast(formMode === "create" ? "Artigo criado com sucesso." : "Artigo atualizado.");
      closeModal();
    } catch (err) {
      console.error(err);
      setFormError("Erro ao salvar o artigo. Verifique os campos ou se o slug já existe.");
    } finally {
      setFormSaving(false);
    }
  };

  const handleReindex = async () => {
    setReindexLoading(true);
    setReindexStats(emptyStats);
    try {
      const res = await fetch("/debug/kb/reindex", { method: "POST" });
      if (!res.ok) {
        throw new Error(`Falha ao reindexar (${res.status})`);
      }
      const data = await res.json();
      setReindexStats(data?.stats || emptyStats);
      showToast("Reindexação concluída.");
    } catch (err) {
      console.error(err);
      setListError("Falha ao reindexar a base. Verifique o backend.");
    } finally {
      setReindexLoading(false);
    }
  };

  const showToast = (message) => {
    setToast(message);
    setTimeout(() => setToast(null), 4000);
  };

  return (
    <div className="app-shell kb-shell">
      <header className="app-header">
        <div className="avatar">KB</div>
        <div className="app-title">
          <h1>Base de conhecimento do Assistente N1</h1>
          <span>Gerencie artigos, tags e reindexação do motor BM25</span>
        </div>
        <div className="badge">
          <div className="badge-dot" />
          Modo administrador
        </div>
      </header>

      <main className="kb-main">
        <section className="kb-card kb-card--intro">
          <div>
            <h2>Gerencie os artigos usados pelo agente</h2>
            <p>
              Cada alteração afeta diretamente o comportamento do assistente após uma nova reindexação.
              Utilize esta área para publicar correções rápidas, ajustar tags e manter a KB alinhada com o time.
            </p>
          </div>
          <div className="kb-card__actions">
            <button className="btn-primary" onClick={openCreateModal}>
              Novo artigo
            </button>
            <button className="btn-accent" onClick={handleReindex} disabled={reindexLoading}>
              {reindexLoading ? "Reindexando..." : "Reindexar KB"}
            </button>
          </div>
          {reindexStats.docs > 0 && (
            <div className="kb-reindex-stats">
              Última reindexação: {reindexStats?.docs ?? 0} artigos | {reindexStats?.chunks ?? 0} trechos
            </div>
          )}
        </section>

        <section className="kb-card">
          <div className="kb-filters">
            <input
              type="text"
              placeholder="Buscar por slug ou título..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
            <label className="kb-checkbox">
              <input
                type="checkbox"
                checked={onlyActive}
                onChange={(e) => setOnlyActive(e.target.checked)}
              />
              Somente ativos
            </label>
            <button className="btn-secondary" onClick={fetchArticles} disabled={listLoading}>
              {listLoading ? "Atualizando..." : "Atualizar"}
            </button>
          </div>

          {listError && <div className="error-banner">{listError}</div>}

          <div className="kb-table-wrapper">
            <table className="kb-table">
              <thead>
                <tr>
                  <th>Slug</th>
                  <th>Título</th>
                  <th>Tags</th>
                  <th>Ativo</th>
                  <th>Ações</th>
                </tr>
              </thead>
              <tbody>
                {listLoading ? (
                  <tr>
                    <td colSpan={5} className="kb-table__empty">
                      Carregando artigos...
                    </td>
                  </tr>
                ) : filteredArticles.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="kb-table__empty">
                      Nenhum artigo encontrado.
                    </td>
                  </tr>
                ) : (
                  filteredArticles.map((article) => (
                    <tr key={article.slug}>
                      <td>
                        <span className="kb-pill">{article.slug}</span>
                      </td>
                      <td>{article.titulo}</td>
                      <td className="kb-tags">
                        {article.tags?.length
                          ? article.tags.join(", ")
                          : <span className="kb-pill kb-pill--muted">Sem tags</span>}
                      </td>
                      <td>
                        <span className={`kb-status ${article.ativo ? "active" : "inactive"}`}>
                          {article.ativo ? "Sim" : "Não"}
                        </span>
                      </td>
                      <td>
                        <button className="btn-primary btn-small" onClick={() => openEditModal(article.slug)}>
                          Editar
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>

      {formVisible && (
        <div className="kb-modal">
          <div className="kb-modal__content">
            <div className="kb-modal__header">
              <h3>{formMode === "create" ? "Novo artigo" : `Editar artigo (${formState.slug})`}</h3>
              <button className="btn-secondary btn-small" onClick={closeModal} disabled={formSaving}>
                Fechar
              </button>
            </div>
            <form className="kb-form" onSubmit={handleSubmit}>
              <label>
                Slug
                <input
                  type="text"
                  value={formState.slug}
                  onChange={(e) => handleFormChange("slug", e.target.value)}
                  placeholder="ex: onedrive_sincronizacao"
                  disabled={formMode === "edit"}
                />
              </label>
              <label>
                Título
                <input
                  type="text"
                  value={formState.titulo}
                  onChange={(e) => handleFormChange("titulo", e.target.value)}
                  placeholder="Nome exibido ao agente"
                />
              </label>
              <label>
                Tags (separe por vírgula)
                <input
                  type="text"
                  value={formState.tagsInput}
                  onChange={(e) => handleFormChange("tagsInput", e.target.value)}
                  placeholder="onedrive, sincronização, arquivos"
                />
              </label>
              <label className="kb-checkbox">
                <input
                  type="checkbox"
                  checked={formState.ativo}
                  onChange={(e) => handleFormChange("ativo", e.target.checked)}
                />
                Artigo ativo
              </label>
              <label>
                Conteúdo (Markdown)
                <textarea
                  className="kb-textarea"
                  value={formState.conteudo_markdown}
                  onChange={(e) => handleFormChange("conteudo_markdown", e.target.value)}
                  rows={12}
                  placeholder="Escreva o conteúdo em Markdown..."
                />
              </label>
              {formError && <div className="error-banner">{formError}</div>}
              <div className="kb-form__actions">
                  <button className="btn-secondary" type="button" onClick={closeModal} disabled={formSaving}>
                    Cancelar
                  </button>
                  <button className="btn-primary" type="submit" disabled={formSaving}>
                    {formSaving ? "Salvando..." : formMode === "create" ? "Criar artigo" : "Salvar alterações"}
                  </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {toast && (
        <div className="kb-toast">
          {toast}
        </div>
      )}
    </div>
  );
}

export default KbAdminPage;
