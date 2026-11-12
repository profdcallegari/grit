import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# E-mails de placeholder que não identificam uma pessoa (ex.: máquinas sem git config)
GENERIC_EMAIL = re.compile(
    r"(^root@|^(unknown|none|nobody)@|@localhost(\.localdomain)?$|@(none|example\.(com|org|net))$|\(none\))",
    re.IGNORECASE,
)


class StudentNormalizer:
    def __init__(self, raw_mailmap: Optional[str] = None, roster_file: Optional[Path] = None):
        # Mapeamentos: email_normalizado -> Nome Canônico
        self.email_to_canonical: Dict[str, str] = {}
        # Mapeamento: (nome_lower, email_lower) -> (Nome Canônico, Email Canônico)
        self.alias_map: Dict[Tuple[str, str], str] = {}

        if raw_mailmap:
            self._parse_mailmap(raw_mailmap)
        if roster_file:
            if roster_file.is_file():
                self._parse_roster(roster_file)
            else:
                logger.warning("Arquivo de turma não encontrado: %s", roster_file)

    def _parse_mailmap(self, text: str):
        # Suporta formatos comuns do .mailmap:
        # Proper Name <proper@email.xx> <commit@email.xx>
        # Proper Name <proper@email.xx> Commit Name <commit@email.xx>
        # <proper@email.xx> <commit@email.xx>
        pattern = re.compile(
            r"^(?:([^<]+?)\s+)?<([^>]+)>\s*(?:([^<]+?)\s+)?(?:<([^>]+)>)?$"
        )
        before = len(self.email_to_canonical)
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = pattern.match(line)
            if not m:
                logger.debug("Linha do .mailmap ignorada (formato não reconhecido): %s", line)
                continue
            prop_name, prop_mail, comm_name, comm_mail = m.groups()
            canonical_name = (prop_name or "").strip()
            target_email = (comm_mail or prop_mail).strip().lower()

            if canonical_name and target_email:
                self.email_to_canonical[target_email] = canonical_name
        logger.info("%d mapeamentos de e-mail carregados do .mailmap.", len(self.email_to_canonical) - before)

    def _parse_roster(self, filepath: Path):
        data = json.loads(filepath.read_text(encoding="utf-8"))
        # Formato esperado: {"Nome do Aluno": ["email1@uf.br", "email2@gmail.com"]}
        before = len(self.email_to_canonical)
        for student_name, emails in data.items():
            for em in emails:
                self.email_to_canonical[em.strip().lower()] = student_name
        logger.info(
            "Arquivo de turma carregado: %d alunos, %d e-mails.",
            len(data), len(self.email_to_canonical) - before,
        )

    def classify_email(self, author_email: str) -> str:
        """Retorna 'ok', 'unmapped' (fora do roster/.mailmap) ou 'generic' (e-mail placeholder)."""
        email_clean = author_email.strip().lower()
        if email_clean in self.email_to_canonical:
            return "ok"
        if GENERIC_EMAIL.search(email_clean):
            return "generic"
        # Sem roster nem .mailmap não há base para afirmar que o e-mail é desconhecido
        if self.email_to_canonical:
            return "unmapped"
        return "ok"

    def get_canonical_identity(self, author_name: str, author_email: str) -> Tuple[str, str]:
        email_clean = author_email.strip().lower()
        if email_clean in self.email_to_canonical:
            return self.email_to_canonical[email_clean], email_clean
        return author_name.strip(), email_clean
    