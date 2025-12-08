import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import kb
from app.kb_admin import (
    KBArticleAlreadyExistsError,
    KBArticleNotFoundError,
    create_kb_article,
    force_reindex,
    get_kb_article,
    list_kb_articles,
    update_kb_article,
)
from app.schemas import KBArticleCreate, KBArticleUpdate


class KBAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.kb_dir = Path(self.tmpdir.name)
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.patch = patch.object(kb, "KB_DIR", self.kb_dir)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmpdir.cleanup()

    def test_create_and_get_article(self):
        payload = KBArticleCreate(
            slug="config_wifi",
            titulo="Configurar Wi-Fi",
            tags=["rede", "wifi"],
            ativo=True,
            conteudo_markdown="# Passo a passo\n1. Abrir painel de controle.",
        )
        created = create_kb_article(payload)
        self.assertEqual(created.slug, "config_wifi")
        self.assertIn("# Passo a passo", created.conteudo_markdown)

        stored = get_kb_article("config_wifi")
        self.assertEqual(stored.titulo, "Configurar Wi-Fi")
        self.assertTrue(stored.ativo)
        self.assertEqual(stored.tags, ["rede", "wifi"])

        items = list_kb_articles()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].slug, "config_wifi")

    def test_update_article_keeps_slug_and_metadata(self):
        payload = KBArticleCreate(
            slug="onedrive_cache",
            titulo="Limpar cache do OneDrive",
            tags=["onedrive"],
            ativo=True,
            conteudo_markdown="# Limpeza\n- Passo único",
        )
        create_kb_article(payload)

        updated = update_kb_article(
            "onedrive_cache",
            KBArticleUpdate(
                slug="onedrive_cache",
                titulo="Limpeza do cache do OneDrive",
                tags=["onedrive", "cache"],
                ativo=False,
                conteudo_markdown="# Atualizado\n1. Feche o app.",
            ),
        )
        self.assertFalse(updated.ativo)
        self.assertEqual(updated.tags, ["onedrive", "cache"])
        self.assertIn("# Atualizado", updated.conteudo_markdown)

    def test_create_duplicate_slug_raises(self):
        payload = KBArticleCreate(
            slug="duplicado",
            titulo="Primeiro",
            tags=[],
            ativo=True,
            conteudo_markdown="Primeiro conteúdo",
        )
        create_kb_article(payload)
        with self.assertRaises(KBArticleAlreadyExistsError):
            create_kb_article(payload)

    def test_update_unknown_slug_raises(self):
        with self.assertRaises(KBArticleNotFoundError):
            update_kb_article(
                "nao_existe",
                KBArticleUpdate(
                    slug="nao_existe",
                    titulo="Nada",
                    tags=[],
                    ativo=True,
                    conteudo_markdown="",
                ),
            )

    def test_force_reindex_returns_stats(self):
        payload = KBArticleCreate(
            slug="kb001",
            titulo="Qualquer",
            tags=[],
            ativo=True,
            conteudo_markdown="Conteúdo simples",
        )
        create_kb_article(payload)
        stats = force_reindex()
        self.assertIn("docs", stats)
        self.assertGreaterEqual(stats.get("docs", 0), 1)
