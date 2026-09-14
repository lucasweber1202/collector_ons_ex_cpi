# collector_ons_ex_cpi

Coletor dos agregados especiais do CPI do Reino Unido publicados pelo ONS,
incluindo medidas de inflação por exclusão (ex-CPI), pesos MM23, índices da
Table 38 e validações das taxas em 12 meses.

## Estado atual

- Mapeamento revisado de 10 medidas de exclusão por CDID nativo do ONS.
- Parser da base MM23 e associação exata com as séries `ALT` da Table 38.
- Validação de pesos complementares e taxas publicadas em 12 meses.
- Tratamento dos dois regimes anuais de pesos e dos vintages de janeiro.
- Infraestrutura-base do coletor CPI preservada enquanto a persistência do
  EX-CPI é concluída.

Consulte `SPECIAL_AGGREGATES.md` para fontes, metodologia, limitações e etapas
restantes antes da persistência em produção.
