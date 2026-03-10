"""
finanalytics_ai.domain.fixed_income.ir_calculator
───────────────────────────────────────────────────
Calculadora de IR para Renda Fixa com tabela regressiva e planejamento de timing.

Tabela regressiva vigente (Lei 11.033/2004):
  Até 180 dias:    22,5%
  181–360 dias:    20,0%
  361–720 dias:    17,5%
  Acima de 720 dias: 15,0%

IOF regressivo (primeiros 29 dias):
  Dia 1: 96% → Dia 29: 3% → Dia 30+: 0%
  IOF só incide sobre o RENDIMENTO, não sobre o principal.

Títulos isentos de IR para Pessoa Física:
  LCI, LCA, CRI, CRA — isenção prevista em lei.
  Tesouro Direto: NÃO isento, incide IR normal.

Design decisions:
  Por que calcular IOF aqui?
    IOF é frequentemente esquecido em planejamento de resgate.
    Para resgates nos primeiros 29 dias, o IOF pode ser maior que o IR,
    tornando a decisão "esperar até o dia 30" altamente relevante.

  Sugestão de "melhor data":
    Calculamos os 4 breakpoints fiscais (180, 360, 720 dias desde compra)
    e recomendamos o próximo que reduz a alíquota. Se já passou de 720,
    não há mais benefício fiscal de timing.

  IR sobre rendimento bruto ou líquido de IOF?
    Pela legislação, IR incide sobre o rendimento APÓS o IOF.
    Implementamos corretamente: base_ir = rendimento - iof_valor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# ── Tabela regressiva de IR ───────────────────────────────────────────────────

IR_TABLE: list[tuple[int, float]] = [
    (180, 0.225),  # até 180 dias
    (360, 0.200),  # 181–360 dias
    (720, 0.175),  # 361–720 dias
    (9999, 0.150),  # acima de 720 dias
]

# IOF regressivo (dia → alíquota sobre rendimento)
_IOF_TABLE: dict[int, float] = {
    1: 0.96,
    2: 0.93,
    3: 0.90,
    4: 0.86,
    5: 0.83,
    6: 0.80,
    7: 0.76,
    8: 0.73,
    9: 0.70,
    10: 0.66,
    11: 0.63,
    12: 0.60,
    13: 0.56,
    14: 0.53,
    15: 0.50,
    16: 0.46,
    17: 0.43,
    18: 0.40,
    19: 0.36,
    20: 0.33,
    21: 0.30,
    22: 0.26,
    23: 0.23,
    24: 0.20,
    25: 0.16,
    26: 0.13,
    27: 0.10,
    28: 0.06,
    29: 0.03,
}

IR_EXEMPT_BOND_TYPES = {"LCI", "LCA", "CRI", "CRA"}


def ir_rate_for_days(days: int) -> float:
    """Alíquota de IR para número de dias corridos desde a aplicação."""
    for threshold, rate in IR_TABLE:
        if days <= threshold:
            return rate
    return 0.15


def iof_rate_for_days(days: int) -> float:
    """Alíquota de IOF para número de dias corridos. Zero a partir do dia 30."""
    return _IOF_TABLE.get(min(days, 29), 0.0) if days < 30 else 0.0


# ── Resultado por data de resgate ─────────────────────────────────────────────


@dataclass
class RedemptionScenario:
    """Cenário de resgate numa data específica."""

    label: str  # "Hoje", "Após 180 dias", etc.
    redemption_date: date
    days_held: int
    gross_value: float  # valor bruto projetado
    gross_yield: float  # rendimento bruto
    iof_rate: float  # alíquota IOF (0 se isento ou dia 30+)
    iof_amount: float  # IOF em R$
    ir_rate: float  # alíquota IR aplicável
    ir_base: float  # base de cálculo do IR (rendimento - IOF)
    ir_amount: float  # IR em R$
    net_value: float  # valor líquido final
    net_yield: float  # rendimento líquido
    net_yield_pct: float  # rendimento líquido %
    net_annual_pct: float  # retorno anualizado líquido %
    total_tax: float  # IOF + IR
    effective_tax_pct: float  # alíquota efetiva sobre rendimento bruto


@dataclass
class IRTimingAdvice:
    """Análise de timing fiscal para um título específico."""

    holding_id: str
    bond_name: str
    bond_type: str
    issuer: str
    invested: float
    purchase_date: date
    maturity_date: date | None
    ir_exempt: bool
    today_scenario: RedemptionScenario
    scenarios: list[RedemptionScenario]  # hoje + 3 breakpoints fiscais
    best_scenario: RedemptionScenario  # melhor relação líquido/taxa
    max_saving: float  # economia máxima vs resgatar hoje
    recommendation: str  # texto da recomendação


def analyze_ir_timing(
    holding_id: str,
    bond_name: str,
    bond_type: str,
    issuer: str,
    invested: float,
    purchase_date: date,
    maturity_date: date | None,
    rate_annual: float,
    rate_pct_indexer: float,
    indexer: str,
    indexer_rate: float,  # CDI/SELIC/IPCA taxa anual decimal
    inflation_rate: float,  # IPCA anual decimal
    today: date | None = None,
) -> IRTimingAdvice:
    """
    Analisa o melhor timing de resgate considerando a tabela regressiva.

    Gera cenários para:
      1. Hoje
      2. Próximo breakpoint de IR (se ainda não atingido)
      3. Todos os breakpoints futuros até o vencimento
      4. Na data de vencimento (se definida)
    """
    today = today or date.today()
    exempt = bond_type in IR_EXEMPT_BOND_TYPES

    def _project_gross(target_date: date) -> float:
        """Projeta valor bruto numa data futura."""
        days = max(1, (target_date - purchase_date).days)
        years = days / 365.0

        if indexer == "CDI":
            daily = (1 + indexer_rate) ** (1 / 252) - 1
            trading_days = int(days * 252 / 365)
            factor = (1 + daily) ** trading_days
            return float(invested * (1 + (factor - 1) * rate_pct_indexer / 100))
        elif indexer == "IPCA":
            real_rate = rate_annual
            return float(invested * (1 + inflation_rate) ** years * (1 + real_rate) ** years)
        else:  # Prefixado
            return float(invested * (1 + rate_annual) ** years)

    def _make_scenario(label: str, target_date: date) -> RedemptionScenario:
        days = max(1, (target_date - purchase_date).days)
        gross = _project_gross(target_date)
        gross_yield = gross - invested

        iof_r = 0.0 if exempt else iof_rate_for_days(days)
        iof_v = round(gross_yield * iof_r, 2)  # arredondado = consistente com iof_amount

        ir_base = max(0.0, gross_yield - iof_v)
        ir_r = 0.0 if exempt else ir_rate_for_days(days)
        ir_v = ir_base * ir_r

        net = gross - iof_v - ir_v
        net_y = net - invested
        net_pct = (net_y / invested * 100) if invested > 0 else 0.0
        years = days / 365.0
        net_ann = ((net / invested) ** (1 / years) - 1) * 100 if years > 0 else 0.0
        eff_tax = ((iof_v + ir_v) / gross_yield * 100) if gross_yield > 0 else 0.0

        return RedemptionScenario(
            label=label,
            redemption_date=target_date,
            days_held=days,
            gross_value=round(gross, 2),
            gross_yield=round(gross_yield, 2),
            iof_rate=iof_r,
            iof_amount=round(iof_v, 2),
            ir_rate=ir_r,
            ir_base=round(ir_base, 2),
            ir_amount=round(ir_v, 2),
            net_value=round(net, 2),
            net_yield=round(net_y, 2),
            net_yield_pct=round(net_pct, 4),
            net_annual_pct=round(net_ann, 4),
            total_tax=round(iof_v + ir_v, 2),
            effective_tax_pct=round(eff_tax, 2),
        )

    # Datas dos breakpoints fiscais desde a compra
    bp_180 = purchase_date + timedelta(days=180)
    bp_360 = purchase_date + timedelta(days=360)
    bp_720 = purchase_date + timedelta(days=720)
    bp_30 = purchase_date + timedelta(days=30)  # fim do IOF

    today_s = _make_scenario("Hoje", today)
    scenarios: list[RedemptionScenario] = [today_s]

    # Só adiciona breakpoints futuros relevantes
    future_breakpoints: list[tuple[str, date]] = []
    if today <= bp_30:
        future_breakpoints.append(("Após 30 dias (fim IOF)", bp_30))
    if today <= bp_180:
        future_breakpoints.append(("Após 180 dias (22,5% → 20%)", bp_180 + timedelta(days=1)))
    if today <= bp_360:
        future_breakpoints.append(("Após 360 dias (20% → 17,5%)", bp_360 + timedelta(days=1)))
    if today <= bp_720:
        future_breakpoints.append(("Após 720 dias (17,5% → 15%)", bp_720 + timedelta(days=1)))

    for label, dt in future_breakpoints:
        # Não ultrapassar o vencimento
        if maturity_date and dt > maturity_date:
            dt = maturity_date
        if dt > today:
            scenarios.append(_make_scenario(label, dt))

    # Na data de vencimento (se diferente dos breakpoints)
    if maturity_date and maturity_date > today and not any(
        s.redemption_date == maturity_date for s in scenarios
    ):
        scenarios.append(_make_scenario("No vencimento", maturity_date))

    # Melhor cenário = maior valor líquido que não ultrapassa vencimento
    valid = [s for s in scenarios if maturity_date is None or s.redemption_date <= maturity_date]
    best = max(valid, key=lambda s: s.net_value)
    max_saving = round(best.net_value - today_s.net_value, 2)

    # Recomendação em linguagem natural
    if exempt:
        rec = (
            f"{bond_name} é isento de IR. "
            "Resgate quando for mais conveniente para sua liquidez — não há impacto fiscal."
        )
    elif best.redemption_date == today:
        rec = "Não há ganho fiscal futuro — você já está na alíquota mínima (15%). Resgate quando precisar."
    elif max_saving < 50:
        rec = (
            f"A economia fiscal potencial é de R$ {max_saving:.2f} — pequena para este montante. "
            "O timing tributário tem pouco impacto neste título."
        )
    else:
        days_wait = (best.redemption_date - today).days
        rec = (
            f"Aguardar até {best.redemption_date.strftime('%d/%m/%Y')} "
            f"({days_wait} dias) pode economizar R$ {max_saving:.2f} em impostos "
            f"(alíquota {today_s.ir_rate * 100:.1f}% → {best.ir_rate * 100:.1f}%)."
        )
        if best.label == "Após 30 dias (fim IOF)":
            rec = (
                f"⚠️ IOF ativo! Aguardar até {best.redemption_date.strftime('%d/%m/%Y')} "
                f"elimina o IOF e economiza R$ {max_saving:.2f}."
            )

    return IRTimingAdvice(
        holding_id=holding_id,
        bond_name=bond_name,
        bond_type=bond_type,
        issuer=issuer,
        invested=invested,
        purchase_date=purchase_date,
        maturity_date=maturity_date,
        ir_exempt=exempt,
        today_scenario=today_s,
        scenarios=scenarios,
        best_scenario=best,
        max_saving=max_saving,
        recommendation=rec,
    )
