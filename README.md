# 🚇 SP-Metrodle

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://metroquiz-hdaq2mlf4mhsvccvjusbpg.streamlit.app/)

Jogue diretamente no navegador pelo Streamlit Community Cloud — sem instalação.

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
   - **Chips de linha** coloridos — chips **sem ✗** indicam linha em comum com a estação secreta.
   - **Distância** em número de estações entre o palpite e a secreta.
   - **Direção** (seta cardinal/diagonal) apontando do seu palpite para a estação secreta.
4. Clique **Nova estação** para jogar novamente — sem limite de rodadas!

## Mapa

O mapa aparece no topo do jogo, centrado na estação secreta (sem revelar seu nome).

- Mostra as linhas do metrô nas **cores oficiais** com geometria real do OpenStreetMap.
- Cada palpite aparece como um **marcador colorido** no mapa.
- A estação secreta é **revelada somente ao fim** da rodada (acerto ou derrota).
- **Requer conexão à internet** para carregar os tiles e a geometria das linhas.
  A geometria é baixada do OpenStreetMap na primeira execução e fica em cache por 7 dias.

### MapTiler (opcional)

Para usar tiles de alta qualidade (visual idêntico ao Metrodle real), adicione sua chave ao arquivo `.streamlit/secrets.toml`:

```toml
maptiler_key = "sua-chave-aqui"
```

Sem a chave, o app usa tiles gratuitos do CartoDB (sem rótulos).

## Modo Difícil

Ative o **🎯 Modo Difícil** na barra lateral:

- As linhas do metrô começam **ocultas** no mapa.
- A cada erro, **uma nova linha** é revelada (na ordem: 1, 2, 3, 4, 5, 15).
- Após **5 erros**, o pan e o zoom do mapa ficam **travados**.

## Linhas cobertas

| Linha | Nome     | Cor     | Estações |
|-------|----------|---------|----------|
| 1     | Azul     | #0455A1 | 21       |
| 2     | Verde    | #007E5E | 14       |
| 3     | Vermelha | #EE372F | 18       |
| 4     | Amarela  | #FFD400 | 10       |
| 5     | Lilás    | #92278F | 14       |
| 15    | Prata    | #9C9C9C | 10       |

**78 estações únicas**, 9 baldeações.

## Estrutura

```
sp-metrodle/
├── data/estacoes.json   # dataset com todas as estações (lat/lon, ordem)
├── game.py              # lógica: BFS, direção, sorteio, avaliação
├── app.py               # interface Streamlit
└── requirements.txt
```
