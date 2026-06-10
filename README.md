# 🚇 SP-Metrodle

Versão local e ilimitada do Metrodle para memorizar as estações do Metrô de São Paulo.

## Instalação

```bash
pip install -r requirements.txt
```

## Como jogar

```bash
streamlit run app.py
```

Acesse `http://localhost:8501` no navegador.

## Regras

1. Uma estação secreta é sorteada entre as 78 estações das **6 linhas em operação**.
2. Você tem **6 tentativas** para adivinhar.
3. Cada palpite mostra:
   - **Linhas em comum** entre o seu palpite e a estação secreta.
   - **Distância** em número de saltos (estações entre uma e outra).
   - **Direção** (seta cardinal/diagonal) apontando do seu palpite para a estação secreta.
4. Clique **Nova estação** para jogar novamente — sem limite de rodadas!

## Linhas cobertas

| Linha | Nome     | Estações |
|-------|----------|----------|
| 1     | Azul     | 21       |
| 2     | Verde    | 14       |
| 3     | Vermelha | 18       |
| 4     | Amarela  | 10       |
| 5     | Lilás    | 14       |
| 15    | Prata    | 10       |

**78 estações únicas**, 9 baldeações.

## Estrutura

```
sp-metrodle/
├── data/estacoes.json   # dataset com todas as estações
├── game.py              # lógica: BFS, direção, sorteio
├── app.py               # interface Streamlit
└── requirements.txt
```
# metroquiz
