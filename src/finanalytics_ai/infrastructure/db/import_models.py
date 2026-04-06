"""
finanalytics_ai.infrastructure.db.import_models
-------------------------------------------------
Modelos para importacao de extratos e notas de corretagem.

Tabelas:
  import_accounts     -- contas por corretora
  import_positions    -- posicao atual por conta
  import_transactions -- historico de operacoes
  import_notes        -- notas de corretagem (cabecalho)
  import_note_items   -- itens da nota (negociacoes)
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey,
    Index, Numeric, String, Text, Integer,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


# ── Contas por corretora ──────────────────────────────────────────────────────

class ImportAccount(Base):
    """Uma conta em uma corretora."""
    __tablename__ = "import_accounts"

    id            = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    corretora     = Column(String(50), nullable=False)          # XP, BTG-BR, BTG-US, Mynt
    conta_numero  = Column(String(50), nullable=True)           # numero da conta
    titular       = Column(String(200), nullable=True)
    moeda         = Column(String(10), default="BRL")
    ativo         = Column(Boolean, default=True)
    criado_em     = Column(DateTime(timezone=True), server_default=func.now())

    positions     = relationship("ImportPosition", back_populates="account",
                                 cascade="all, delete-orphan")
    transactions  = relationship("ImportTransaction", back_populates="account",
                                 cascade="all, delete-orphan")
    notes         = relationship("ImportNote", back_populates="account",
                                 cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("corretora", "conta_numero", name="uq_account_corretora_conta"),
    )

    def __repr__(self) -> str:
        return f"<ImportAccount {self.corretora} {self.conta_numero}>"


# ── Posicoes atuais ───────────────────────────────────────────────────────────

class ImportPosition(Base):
    """Posicao atual de um ativo em uma conta."""
    __tablename__ = "import_positions"

    id             = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    account_id     = Column(UUID(as_uuid=False), ForeignKey("import_accounts.id",
                            ondelete="CASCADE"), nullable=False)
    ticker         = Column(String(100), nullable=False)
    nome           = Column(String(300), nullable=True)
    tipo           = Column(String(30), nullable=False)         # acao, fii, fundo, cdb, cra, cri, deb, ntnb, cripto, etf, bdr, fip, lci, lca, coe, alt, emprestimo, stock
    moeda          = Column(String(10), default="BRL")
    quantidade     = Column(Numeric(20, 8), default=0)
    preco_medio    = Column(Numeric(20, 6), nullable=True)      # custo medio por unidade
    preco_atual    = Column(Numeric(20, 6), nullable=True)      # ultimo preco conhecido
    valor_bruto    = Column(Numeric(20, 2), nullable=True)      # saldo bruto
    valor_liquido  = Column(Numeric(20, 2), nullable=True)      # saldo liquido (deduzido IR/IOF)
    valor_aplicado = Column(Numeric(20, 2), nullable=True)      # capital original investido
    taxa           = Column(String(100), nullable=True)         # ex: "CDI+3%", "IPCA+7.5%"
    vencimento     = Column(Date, nullable=True)
    data_compra    = Column(Date, nullable=True)
    data_referencia = Column(Date, nullable=True)               # data do extrato
    atualizado_em  = Column(DateTime(timezone=True), server_default=func.now(),
                            onupdate=func.now())

    account        = relationship("ImportAccount", back_populates="positions")

    __table_args__ = (
        Index("ix_pos_account_ticker", "account_id", "ticker"),
        Index("ix_pos_tipo", "tipo"),
    )

    def __repr__(self) -> str:
        return f"<ImportPosition {self.ticker} {self.quantidade}>"


# ── Transacoes (historico) ────────────────────────────────────────────────────

class ImportTransaction(Base):
    """Uma operacao individual (compra, venda, provento, etc.)."""
    __tablename__ = "import_transactions"

    id             = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    account_id     = Column(UUID(as_uuid=False), ForeignKey("import_accounts.id",
                            ondelete="CASCADE"), nullable=False)
    data_operacao  = Column(Date, nullable=False)
    data_liquidacao = Column(Date, nullable=True)
    tipo           = Column(String(30), nullable=False)         # compra, venda, dividendo, jcp, amortizacao, deposito, saque, taxa, emprestimo
    ticker         = Column(String(100), nullable=False)
    nome           = Column(String(300), nullable=True)
    quantidade     = Column(Numeric(20, 8), nullable=True)
    preco_unitario = Column(Numeric(20, 6), nullable=True)
    valor_bruto    = Column(Numeric(20, 2), nullable=True)
    ir             = Column(Numeric(20, 2), default=0)
    iof            = Column(Numeric(20, 2), default=0)
    valor_liquido  = Column(Numeric(20, 2), nullable=True)
    moeda          = Column(String(10), default="BRL")
    nota_id        = Column(UUID(as_uuid=False), ForeignKey("import_notes.id",
                            ondelete="SET NULL"), nullable=True)
    observacao     = Column(Text, nullable=True)
    criado_em      = Column(DateTime(timezone=True), server_default=func.now())

    account        = relationship("ImportAccount", back_populates="transactions")
    note           = relationship("ImportNote", back_populates="transactions")

    __table_args__ = (
        Index("ix_tx_account_data", "account_id", "data_operacao"),
        Index("ix_tx_ticker", "ticker"),
        Index("ix_tx_tipo", "tipo"),
    )


# ── Notas de corretagem ───────────────────────────────────────────────────────

class ImportNote(Base):
    """
    Cabecalho de uma nota de corretagem.

    Armazena todos os custos:
      - corretagem (taxa operacional)
      - taxa_registro (taxa registro BM&F)
      - emolumentos (taxas BM&F: emol + f.garantia)
      - irrf / irrf_daytrade
      - outros_custos
      - ajuste_posicao
      - ajuste_daytrade
      - total_custos_operacionais
      - total_liquido (resultado final da nota)
    """
    __tablename__ = "import_notes"

    id                       = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    account_id               = Column(UUID(as_uuid=False), ForeignKey("import_accounts.id",
                                     ondelete="CASCADE"), nullable=False)
    numero_nota              = Column(String(50), nullable=True)
    data_pregao              = Column(Date, nullable=False)
    corretora                = Column(String(50), nullable=False)
    corretora_cnpj           = Column(String(20), nullable=True)
    cliente_cpf              = Column(String(20), nullable=True)
    folha                    = Column(Integer, default=1)

    # Resultados brutos
    valor_negocios           = Column(Numeric(20, 2), default=0)  # valor dos negocios (D/C)
    venda_disponivel         = Column(Numeric(20, 2), default=0)
    compra_disponivel        = Column(Numeric(20, 2), default=0)
    venda_opcoes             = Column(Numeric(20, 2), default=0)
    compra_opcoes            = Column(Numeric(20, 2), default=0)
    ajuste_posicao           = Column(Numeric(20, 2), default=0)
    ajuste_daytrade          = Column(Numeric(20, 2), default=0)

    # Custos separados
    corretagem               = Column(Numeric(20, 2), default=0)  # taxa operacional
    taxa_registro            = Column(Numeric(20, 2), default=0)  # taxa registro BM&F
    emolumentos              = Column(Numeric(20, 2), default=0)  # taxas BM&F (emol+f.gar)
    irrf                     = Column(Numeric(20, 2), default=0)
    irrf_daytrade            = Column(Numeric(20, 2), default=0)
    outros_custos            = Column(Numeric(20, 2), default=0)
    impostos                 = Column(Numeric(20, 2), default=0)
    total_custos_operacionais = Column(Numeric(20, 2), default=0)

    # Totais
    total_liquido            = Column(Numeric(20, 2), default=0)  # resultado final (D=negativo, C=positivo)
    sinal_total              = Column(String(1), default="D")     # D=debito, C=credito
    arquivo_original         = Column(String(500), nullable=True)

    criado_em                = Column(DateTime(timezone=True), server_default=func.now())

    account      = relationship("ImportAccount", back_populates="notes")
    items        = relationship("ImportNoteItem", back_populates="note",
                               cascade="all, delete-orphan")
    transactions = relationship("ImportTransaction", back_populates="note")

    __table_args__ = (
        UniqueConstraint("account_id", "numero_nota", "data_pregao",
                         name="uq_note_account_numero_data"),
        Index("ix_note_data", "data_pregao"),
    )

    @property
    def resultado_bruto(self) -> float:
        """Resultado antes dos custos (C=positivo, D=negativo)."""
        v = float(self.valor_negocios or 0)
        return v if self.sinal_total == "C" else -v

    @property
    def resultado_liquido(self) -> float:
        v = float(self.total_liquido or 0)
        return v if self.sinal_total == "C" else -v


class ImportNoteItem(Base):
    """Uma negociacao dentro da nota de corretagem."""
    __tablename__ = "import_note_items"

    id             = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    note_id        = Column(UUID(as_uuid=False), ForeignKey("import_notes.id",
                            ondelete="CASCADE"), nullable=False)
    cv             = Column(String(1), nullable=False)          # C ou V
    mercadoria     = Column(String(100), nullable=False)        # WDO J26, PETR4, etc.
    vencimento     = Column(Date, nullable=True)
    quantidade     = Column(Numeric(20, 4), nullable=False)
    preco          = Column(Numeric(20, 6), nullable=False)
    tipo_negocio   = Column(String(50), nullable=True)          # DAY TRADE, NORMAL, etc.
    valor_operacao = Column(Numeric(20, 2), nullable=False)
    dc             = Column(String(1), default="D")             # D ou C
    taxa_operacional = Column(Numeric(20, 2), default=0)

    note           = relationship("ImportNote", back_populates="items")

    def __repr__(self) -> str:
        return f"<NoteItem {self.cv} {self.mercadoria} {self.quantidade}@{self.preco}>"
