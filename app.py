import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import date, timedelta
import warnings

# Configuração da Página
st.set_page_config(
    page_title="Simulador de Opções B3 (Pro)",
    page_icon="📊",
    layout="wide"
)

# Ignorar avisos de versão
warnings.simplefilter(action='ignore', category=FutureWarning)

# --- CSS CUSTOMIZADO ---
st.markdown("""
<style>
    .metric-card {
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.05);
    }
    .metric-label {
        font-size: 14px;
        color: #6c757d;
        text-transform: uppercase;
        font-weight: 600;
    }
    .metric-value {
        font-size: 26px;
        font-weight: bold;
        margin-top: 5px;
    }
    .positive { color: #28a745; }
    .negative { color: #dc3545; }
    .neutral { color: #6c757d; }
    .warning { color: #ffc107; }
</style>
""", unsafe_allow_html=True)

# --- CACHE DE DADOS ---
@st.cache_data(ttl=3600)
def baixar_dados(ticker, inicio, fim):
    try:
        fim_ajustado = fim + timedelta(days=200)
        df = yf.download(ticker, start=inicio, end=fim_ajustado, progress=False, auto_adjust=False)
        
        if df.empty: return df
        if isinstance(df.columns, pd.MultiIndex):
            try:
                df.columns = df.columns.get_level_values('Price')
            except:
                df.columns = df.columns.get_level_values(0)
        
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
            
        return df
    except Exception as e:
        return pd.DataFrame()

# --- MOTOR DE CÁLCULO FINANCEIRO ---
def calcular_estrategia(data, params):
    ticker = params['ticker']
    qtde = params['qtde']
    posicao = params['posicao'] # 'Comprado' ou 'Vendido'
    tipo = params['tipo']       # 'Call', 'Put', 'Straddle'
    premio_pct_perna = params['premio_pct'] / 100.0
    strike_offset_pct = params['strike_offset'] / 100.0
    dias_hold = params['dias_hold']
    
    # Parâmetros de Custo B3
    taxa_entrada = 0.005  # 0.5% sobre o prêmio
    taxa_exercicio = 0.005 # 0.5% sobre o strike
    ir_aliquota = 0.15
    
    col_preco = 'Close'
    if 'Close' not in data.columns:
        col_preco = 'Adj Close' if 'Adj Close' in data.columns else None
    
    if not col_preco: return None, "Coluna de preço não encontrada."

    # Filtra Período
    mask = (data.index.date >= params['inicio']) & (data.index.date <= params['fim'])
    data_sim = data.loc[mask]
    
    if len(data_sim) == 0: return None, "Sem dados no período."

    indices_possiveis = [i for i, dt in enumerate(data.index) if dt.date() >= params['inicio'] and dt.date() <= params['fim']]
    
    if not indices_possiveis: return None, "Intervalo inválido."

    trades = []
    prejuizo_acumulado = 0.0
    ultimo_idx_valido = len(data) - dias_hold
    current_idx = indices_possiveis[0]
    
    while current_idx < ultimo_idx_valido:
        if data.index[current_idx].date() > params['fim']: break
            
        try:
            # 1. Dados de Mercado (Entrada/Saída)
            entry_date = data.index[current_idx]
            entry_price = float(data[col_preco].iloc[current_idx])
            
            exit_idx = current_idx + dias_hold
            exit_date = data.index[exit_idx]
            exit_price = float(data[col_preco].iloc[exit_idx])
            
            # 2. Definição Inteligente de Strikes
            strike_call = 0.0
            strike_put = 0.0
            
            # Lógica:
            # Se for Straddle/Combo, mantém simetria (Ex: Offset 5% -> Call +5%, Put -5%)
            # Se for Perna Única, respeita o sinal (Ex: Call -5% -> Call ITM; Put -5% -> Put OTM)
            
            if tipo == 'Straddle (Call + Put)':
                offset_abs = abs(strike_offset_pct)
                strike_call = entry_price * (1 + offset_abs)
                strike_put = entry_price * (1 - offset_abs)
            else:
                # Perna única: usa o valor exato do slider (pode ser negativo ou positivo)
                strike_unico = entry_price * (1 + strike_offset_pct)
                if tipo == 'Call': strike_call = strike_unico
                if tipo == 'Put': strike_put = strike_unico

            # 3. Definição de Pernas Ativas
            usar_call = (tipo == 'Call' or tipo == 'Straddle (Call + Put)')
            usar_put = (tipo == 'Put' or tipo == 'Straddle (Call + Put)')
            
            # 4. Cálculo Financeiro
            financeiro_premio_total = 0.0
            
            if usar_call: financeiro_premio_total += (entry_price * premio_pct_perna) * qtde
            if usar_put:  financeiro_premio_total += (entry_price * premio_pct_perna) * qtde
            
            custo_entrada = financeiro_premio_total * taxa_entrada
            
            # 5. Payoff e Exercício
            payoff_total = 0.0
            custo_exercicio = 0.0
            
            if usar_call:
                payoff = max(0, exit_price - strike_call)
                payoff_total += payoff * qtde
                if exit_price > strike_call: # Exerceu Call
                    custo_exercicio += (strike_call * qtde) * taxa_exercicio
            
            if usar_put:
                payoff = max(0, strike_put - exit_price)
                payoff_total += payoff * qtde
                if exit_price < strike_put: # Exerceu Put
                    custo_exercicio += (strike_put * qtde) * taxa_exercicio
            
            custos_totais = custo_entrada + custo_exercicio
            
            # 6. Resultado Final
            if posicao == 'Comprado (Titular)':
                # Ganha Payoff, Paga Prêmio + Taxas
                res_op = payoff_total - financeiro_premio_total - custos_totais
            else: # Vendido (Lançador)
                # Ganha Prêmio, Paga Payoff + Taxas
                res_op = financeiro_premio_total - payoff_total - custos_totais
            
            # 7. IR
            ir = 0.0
            if res_op > 0:
                lucro_real = max(0, res_op - prejuizo_acumulado)
                abatimento = res_op - lucro_real
                prejuizo_acumulado -= abatimento
                ir = lucro_real * ir_aliquota
            else:
                prejuizo_acumulado += abs(res_op)
            
            liquido = res_op - ir
            
            # Determina qual strike exibir na tabela
            strike_display = 0.0
            if tipo == 'Call': strike_display = strike_call
            elif tipo == 'Put': strike_display = strike_put
            else: strike_display = 0.0 # Straddle mostra 0 ou tratamos visualmente depois

            trades.append({
                'Entrada': entry_date,
                'Preço Ent.': entry_price,
                'Strike': strike_display, # Coluna Nova
                'Saída': exit_date,
                'Preço Sai.': exit_price,
                'Prêmio': financeiro_premio_total,
                'Custos': custos_totais,
                'Res. Oper.': res_op,
                'IR': ir,
                'Líquido': liquido
            })
            
        except Exception: pass
        
        current_idx += dias_hold
        
    return pd.DataFrame(trades), None

# --- INTERFACE ---

st.sidebar.header("⚙️ Configuração")

# 1. Inputs Básicos
ticker = st.sidebar.text_input("Ticker", "PETR4.SA").upper().strip()
qtde = st.sidebar.number_input("Lote (Qtde)", min_value=100, value=1000, step=100)

# 2. Estratégia
st.sidebar.markdown("---")
tipo = st.sidebar.selectbox("Tipo de Opção", ['Call', 'Put', 'Straddle (Call + Put)'])
posicao = st.sidebar.selectbox("Sua Posição", ['Comprado (Titular)', 'Vendido (Lançador)'])

# 3. Janela Temporal
st.sidebar.markdown("---")
c1, c2 = st.sidebar.columns(2)
inicio = c1.date_input("Início", date.today() - timedelta(days=365))
fim = c2.date_input("Fim", date.today())

# 4. Parâmetros Avançados
st.sidebar.markdown("---")
st.sidebar.markdown("**Parâmetros da Opção**")

dias_hold = st.sidebar.slider("Dias até Vencimento", 5, 90, 20)
premio_pct = st.sidebar.slider("Prêmio p/ Perna (% do Ativo)", 0.1, 15.0, 3.0, step=0.1, help="Quanto custa cada opção em % do preço da ação hoje.")

# SLIDER IMPORTANTE: Strike Relativo
strike_offset = st.sidebar.slider(
    "Strike vs Preço Atual (%)", 
    -30.0, 30.0, 0.0, step=0.5,
    help="Negativo = Strike Abaixo do Preço. Positivo = Strike Acima do Preço. (No Straddle, define o intervalo)."
)

# Botão
if st.sidebar.button("🚀 Simular Estratégia", type="primary"):
    if inicio >= fim:
        st.error("Data final deve ser maior que inicial.")
    else:
        with st.spinner(f"Processando {ticker}..."):
            df_dados = baixar_dados(ticker, inicio, fim)
            
        if df_dados.empty:
            st.error("Dados não encontrados.")
        else:
            params = {
                'ticker': ticker, 'qtde': qtde, 'posicao': posicao, 'tipo': tipo,
                'inicio': inicio, 'fim': fim, 'dias_hold': dias_hold,
                'premio_pct': premio_pct, 'strike_offset': strike_offset
            }
            
            df_res, erro = calcular_estrategia(df_dados, params)
            
            if erro: st.warning(erro)
            elif df_res.empty: st.warning("Nenhuma operação gerada.")
            else:
                # --- RESULTADOS ---
                custo_txt = f"{premio_pct}%" if tipo != 'Straddle (Call + Put)' else f"{premio_pct*2:.1f}% (Total)"
                st.subheader(f"Resultado: {ticker} | {tipo} ({posicao})")
                st.caption(f"Strike Offset: {strike_offset}% | Custo: {custo_txt} | Prazo: {dias_hold} dias")
                
                tot_liq = df_res['Líquido'].sum()
                tot_custos = df_res['Custos'].sum()
                tot_ir = df_res['IR'].sum()
                win_rate = (len(df_res[df_res['Res. Oper.'] > 0]) / len(df_res)) * 100
                
                cor = "positive" if tot_liq > 0 else "negative"
                
                # Cards
                st.markdown(f"""
                <div style="display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 20px;">
                    <div class="metric-card" style="flex: 1;">
                        <div class="metric-label">Líquido Final</div>
                        <div class="metric-value {cor}">R$ {tot_liq:,.2f}</div>
                    </div>
                    <div class="metric-card" style="flex: 1;">
                        <div class="metric-label">Custos Totais</div>
                        <div class="metric-value warning">R$ {tot_custos:,.2f}</div>
                    </div>
                    <div class="metric-card" style="flex: 1;">
                        <div class="metric-label">IR (15%)</div>
                        <div class="metric-value negative">R$ {tot_ir:,.2f}</div>
                    </div>
                    <div class="metric-card" style="flex: 1;">
                        <div class="metric-label">Win Rate</div>
                        <div class="metric-value neutral">{win_rate:.1f}%</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                # Tabela
                st.markdown("### 📋 Detalhamento")
                
                cols = ['Entrada', 'Preço Ent.', 'Strike', 'Saída', 'Preço Sai.', 'Prêmio', 'Custos', 'Res. Oper.', 'Líquido']
                df_show = df_res[cols].copy()
                
                # Se for Straddle, a coluna Strike fica confusa (são 2 strikes), então zeramos visualmente ou removemos
                if tipo == 'Straddle (Call + Put)':
                    df_show.drop(columns=['Strike'], inplace=True)
                
                # Formatação datas
                df_show['Entrada'] = df_show['Entrada'].dt.strftime('%d/%m/%Y')
                df_show['Saída'] = df_show['Saída'].dt.strftime('%d/%m/%Y')
                
                # Formatação Moeda
                f_moeda = lambda x: f"R$ {x:,.2f}"
                cols_moeda = [c for c in df_show.columns if c not in ['Entrada', 'Saída']]
                
                st.dataframe(
                    df_show.style.format({c: f_moeda for c in cols_moeda})
                           .map(lambda x: 'color: green' if x > 0 else 'color: red', subset=['Res. Oper.', 'Líquido']),
                    use_container_width=True,
                    height=500
                )
                
                # Download
                csv = df_res.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Download CSV", csv, "backtest_opcoes.csv", "text/csv")
